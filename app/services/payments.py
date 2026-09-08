"""Orquestación del ciclo de vida de un pago."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.exceptions import ConflictError, GatewayError, NotFoundError
from app.db.models import FraudAssessment, Payment
from app.db.models.payment import new_reference
from app.schemas.enums import PaymentStatus, RiskDecision
from app.schemas.fraud import FraudEvaluationResponse
from app.schemas.payment import PaymentCreate
from app.services.fraud import FraudContext, FraudEngine, fraud_engine
from app.services.stripe_gateway import StripeGateway, stripe_gateway

logger = logging.getLogger(__name__)

# Estados que cuentan como intento fallido al medir el riesgo del cliente.
_FAILED_STATUSES = (PaymentStatus.FAILED, PaymentStatus.REJECTED)


class PaymentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        engine: FraudEngine | None = None,
        gateway: StripeGateway | None = None,
        config: Settings | None = None,
    ) -> None:
        self._session = session
        self._engine = engine or fraud_engine
        self._gateway = gateway or stripe_gateway
        self._settings = config or default_settings

    # --- Contexto de riesgo -------------------------------------------------

    async def build_fraud_context(
        self, data: PaymentCreate, *, now: datetime | None = None
    ) -> FraudContext:
        """Reúne los agregados históricos del cliente para puntuar el riesgo."""
        moment = now or datetime.now(UTC)
        last_hour = moment - timedelta(hours=1)
        last_day = moment - timedelta(days=1)

        totals = await self._session.execute(
            select(
                func.count(Payment.id),
                func.count(Payment.id).filter(Payment.created_at >= last_hour),
                func.count(Payment.id).filter(
                    Payment.created_at >= last_day, Payment.status.in_(_FAILED_STATUSES)
                ),
                func.count(func.distinct(Payment.card_bin)).filter(Payment.created_at >= last_day),
                func.avg(Payment.amount_minor).filter(Payment.status == PaymentStatus.SUCCEEDED),
            ).where(Payment.customer_id == data.customer_id)
        )
        total, last_hour_count, failed_count, distinct_cards, avg_amount = totals.one()

        return FraudContext(
            amount_minor=data.amount_minor,
            currency=data.currency,
            customer_id=data.customer_id,
            email=data.email,
            card_bin=data.card_bin,
            card_country=data.card_country,
            ip_country=data.ip_country,
            occurred_at=moment,
            is_new_customer=total == 0,
            payments_last_hour=last_hour_count or 0,
            failed_payments_last_day=failed_count or 0,
            distinct_cards_last_day=distinct_cards or 0,
            average_amount_minor=int(avg_amount) if avg_amount else None,
        )

    # --- Casos de uso -------------------------------------------------------

    async def create_payment(
        self, data: PaymentCreate, *, idempotency_key: str | None = None
    ) -> Payment:
        """Crea un pago, lo puntúa y —si procede— lo envía a la pasarela.

        La respuesta es siempre un pago: un rechazo por riesgo no es un error de
        la petición, sino un resultado legítimo reflejado en `status`.
        """
        if idempotency_key:
            existing = await self.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                # Reintento del cliente: devolvemos el pago original sin recobrar.
                if existing.amount_minor != data.amount_minor or existing.currency != data.currency:
                    raise ConflictError(
                        "La clave de idempotencia ya se usó con otro importe o divisa.",
                        details={"idempotency_key": idempotency_key},
                    )
                return existing

        assessment = self._engine.evaluate(await self.build_fraud_context(data))

        payment = Payment(
            reference=new_reference(),
            customer_id=data.customer_id,
            amount_minor=data.amount_minor,
            currency=data.currency,
            method=data.method,
            email=data.email,
            ip_address=data.ip_address,
            card_bin=data.card_bin,
            card_country=data.card_country,
            ip_country=data.ip_country,
            idempotency_key=idempotency_key,
            risk_score=assessment.score,
            risk_decision=assessment.decision,
            status=self._initial_status(assessment.decision),
            meta=data.meta,
        )
        payment.assessments.append(_to_model(assessment))
        self._session.add(payment)
        await self._session.flush()

        if assessment.decision is RiskDecision.APPROVE:
            await self._authorize(payment)

        await self._session.commit()
        await self._session.refresh(payment)

        logger.info(
            "payment.created",
            extra={
                "reference": payment.reference,
                "status": payment.status,
                "risk_score": payment.risk_score,
                "risk_decision": payment.risk_decision,
            },
        )
        return payment

    async def _authorize(self, payment: Payment) -> None:
        """Envía el cobro a la pasarela y refleja el resultado en el pago."""
        try:
            charge = await self._gateway.create_payment_intent(
                amount_minor=payment.amount_minor,
                currency=payment.currency,
                reference=payment.reference,
                customer_id=payment.customer_id,
                idempotency_key=payment.idempotency_key,
            )
        except GatewayError as exc:
            # El pago queda como fallido y auditable en vez de perderse: el
            # cliente puede reintentar con la misma clave de idempotencia.
            payment.status = PaymentStatus.FAILED
            payment.failure_reason = exc.message[:255]
            return

        payment.provider_payment_id = charge.provider_payment_id
        if charge.succeeded:
            payment.status = PaymentStatus.SUCCEEDED
            payment.settled_at = datetime.now(UTC)
        else:
            payment.status = PaymentStatus.PROCESSING

    async def refund(
        self, reference: str, *, amount_minor: int | None = None, reason: str | None = None
    ) -> Payment:
        payment = await self.get_by_reference(reference)
        if payment is None:
            raise NotFoundError(f"No existe el pago {reference}.")
        if payment.status is not PaymentStatus.SUCCEEDED:
            raise ConflictError(
                f"Solo se pueden devolver pagos cobrados; estado actual: {payment.status}.",
                details={"reference": reference, "status": payment.status},
            )
        if amount_minor is not None and amount_minor > payment.amount_minor:
            raise ConflictError("El importe a devolver supera el importe cobrado.")
        if payment.provider_payment_id is None:
            raise ConflictError("El pago no tiene identificador en la pasarela.")

        await self._gateway.refund(
            provider_payment_id=payment.provider_payment_id,
            amount_minor=amount_minor,
            reason=reason,
        )
        payment.status = PaymentStatus.REFUNDED
        await self._session.commit()
        await self._session.refresh(payment)
        return payment

    async def mark_under_review_decision(self, reference: str, approve: bool) -> Payment:
        """Resuelve manualmente un pago retenido por el motor de riesgo."""
        payment = await self.get_by_reference(reference)
        if payment is None:
            raise NotFoundError(f"No existe el pago {reference}.")
        if payment.status is not PaymentStatus.UNDER_REVIEW:
            raise ConflictError(
                f"El pago no está en revisión; estado actual: {payment.status}.",
                details={"status": payment.status},
            )

        if approve:
            await self._authorize(payment)
        else:
            payment.status = PaymentStatus.REJECTED
            payment.failure_reason = "Rechazado en revisión manual."

        await self._session.commit()
        await self._session.refresh(payment)
        return payment

    # --- Consultas ----------------------------------------------------------

    async def get_by_reference(self, reference: str) -> Payment | None:
        result = await self._session.execute(
            select(Payment).where(Payment.reference == reference)
        )
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(self, key: str) -> Payment | None:
        result = await self._session.execute(
            select(Payment).where(Payment.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def list_payments(
        self,
        *,
        customer_id: str | None = None,
        status: PaymentStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Payment], int]:
        filters = []
        if customer_id:
            filters.append(Payment.customer_id == customer_id)
        if status:
            filters.append(Payment.status == status)

        total = await self._session.scalar(
            select(func.count(Payment.id)).where(*filters)
        )
        rows = await self._session.execute(
            select(Payment)
            .where(*filters)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows.scalars().all()), int(total or 0)

    def _initial_status(self, decision: RiskDecision) -> PaymentStatus:
        return {
            RiskDecision.APPROVE: PaymentStatus.PENDING,
            RiskDecision.REVIEW: PaymentStatus.UNDER_REVIEW,
            RiskDecision.REJECT: PaymentStatus.REJECTED,
        }[decision]


def _to_model(assessment: FraudEvaluationResponse) -> FraudAssessment:
    return FraudAssessment(
        score=assessment.score,
        decision=assessment.decision,
        engine_version=assessment.engine_version,
        signals=[signal.model_dump() for signal in assessment.signals],
    )
