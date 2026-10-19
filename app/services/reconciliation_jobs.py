"""Generación de informes de conciliación acotados por comercio y periodo."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.db.models import (
    Merchant,
    Payment,
    Receipt,
    ReconciliationJob,
    WebhookEvent,
)
from app.schemas.enums import (
    DiscrepancyKind,
    JobStatus,
    ReceiptStatus,
    ReconciliationStatus,
)


class ReconciliationJobService:
    def __init__(self, session: AsyncSession, *, merchant: Merchant) -> None:
        self._session = session
        self._merchant = merchant

    async def create(self, period_start: datetime, period_end: datetime) -> ReconciliationJob:
        job = ReconciliationJob(
            merchant_id=self._merchant.id,
            period_start=period_start,
            period_end=period_end,
            status=JobStatus.RUNNING,
        )
        self._session.add(job)
        await self._session.flush()

        payments = list(
            (
                await self._session.scalars(
                    select(Payment)
                    .where(
                        Payment.merchant_id == self._merchant.id,
                        Payment.created_at >= period_start,
                        Payment.created_at < period_end,
                    )
                    .order_by(Payment.created_at, Payment.id)
                )
            ).all()
        )
        payment_ids = {payment.id for payment in payments}

        receipts = list(
            (
                await self._session.scalars(
                    select(Receipt)
                    .where(
                        Receipt.merchant_id == self._merchant.id,
                        Receipt.created_at >= period_start,
                        Receipt.created_at < period_end,
                    )
                    .order_by(Receipt.created_at, Receipt.id)
                )
            ).all()
        )
        receipts_by_payment: dict[uuid.UUID, list[Receipt]] = {}
        for receipt in receipts:
            if receipt.payment_id in payment_ids:
                receipts_by_payment.setdefault(receipt.payment_id, []).append(receipt)

        discrepancies: list[dict[str, Any]] = []
        matched = mismatched = pending = duplicate = 0
        for payment in payments:
            linked = receipts_by_payment.get(payment.id, [])
            if not linked:
                pending += 1
                discrepancies.append(
                    self._discrepancy(
                        DiscrepancyKind.MISSING_RECEIPT,
                        payment_reference=payment.reference,
                        detail="No se encontró un comprobante del periodo para el pago.",
                    )
                )
                continue

            statuses = {receipt.reconciliation_status for receipt in linked}
            if ReconciliationStatus.DUPLICATE in statuses:
                duplicate += 1
                for receipt in linked:
                    if receipt.reconciliation_status is ReconciliationStatus.DUPLICATE:
                        discrepancies.append(
                            self._discrepancy(
                                DiscrepancyKind.DUPLICATE_RECEIPT,
                                payment_reference=payment.reference,
                                receipt_id=receipt.id,
                                detail="El comprobante ya fue usado en otra conciliación.",
                            )
                        )
            elif ReconciliationStatus.MISMATCHED in statuses:
                mismatched += 1
                for receipt in linked:
                    if receipt.reconciliation_status is ReconciliationStatus.MISMATCHED:
                        discrepancies.append(
                            self._discrepancy(
                                DiscrepancyKind.AMOUNT_MISMATCH,
                                payment_reference=payment.reference,
                                receipt_id=receipt.id,
                                detail="El importe o los datos extraídos no coinciden con el pago.",
                            )
                        )
            elif ReconciliationStatus.MATCHED in statuses:
                matched += 1
            else:
                pending += 1
                for receipt in linked:
                    if (
                        receipt.status is ReceiptStatus.FAILED
                        or receipt.reconciliation_status is ReconciliationStatus.UNREADABLE
                    ):
                        discrepancies.append(
                            self._discrepancy(
                                DiscrepancyKind.UNREADABLE_RECEIPT,
                                payment_reference=payment.reference,
                                receipt_id=receipt.id,
                                detail="No se pudo extraer información legible del comprobante.",
                            )
                        )

        unmatched_receipts = [
            receipt for receipt in receipts if receipt.payment_id not in payment_ids
        ]
        for receipt in unmatched_receipts:
            discrepancies.append(
                self._discrepancy(
                    DiscrepancyKind.ORPHAN_RECEIPT,
                    receipt_id=receipt.id,
                    detail="El comprobante no está asociado a un pago del periodo.",
                )
            )

        merchant_provider_payment_ids = {
            (payment.provider, str(payment.provider_payment_id))
            for payment in payments
            if payment.provider_payment_id is not None
        }
        events = list(
            (
                await self._session.scalars(
                    select(WebhookEvent).where(
                        WebhookEvent.merchant_id == self._merchant.id,
                        WebhookEvent.created_at >= period_start,
                        WebhookEvent.created_at < period_end,
                    )
                )
            ).all()
        )
        for event in events:
            event_data = event.payload.get("data", {})
            if not isinstance(event_data, dict):
                event_data = {}
            obj = event_data.get("object", event_data)
            if not isinstance(obj, dict):
                obj = {}
            provider_id = obj.get("payment_intent") or obj.get("id")
            if (
                provider_id is not None
                and (event.provider, str(provider_id)) in merchant_provider_payment_ids
            ):
                continue
            discrepancies.append(
                self._discrepancy(
                    DiscrepancyKind.GATEWAY_WITHOUT_PAYMENT,
                    webhook_event_id=event.event_id,
                    detail="Evento de pasarela del comercio sin pago asociado.",
                )
            )

        job.total_payments = len(payments)
        job.matched_count = matched
        job.mismatched_count = mismatched
        job.pending_count = pending
        job.duplicate_count = duplicate
        job.unmatched_receipts_count = len(unmatched_receipts)
        job.discrepancies = discrepancies
        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(job)
        return job

    async def get(self, job_id: uuid.UUID) -> ReconciliationJob:
        job = await self._session.scalar(
            select(ReconciliationJob).where(
                ReconciliationJob.id == job_id,
                ReconciliationJob.merchant_id == self._merchant.id,
            )
        )
        if job is None:
            raise NotFoundError(f"No existe el trabajo de conciliación {job_id}.")
        return job

    @staticmethod
    def _discrepancy(
        kind: DiscrepancyKind,
        *,
        detail: str,
        payment_reference: str | None = None,
        receipt_id: uuid.UUID | None = None,
        webhook_event_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "kind": kind.value,
            "detail": detail,
            "payment_reference": payment_reference,
            "receipt_id": str(receipt_id) if receipt_id else None,
            "webhook_event_id": webhook_event_id,
        }
