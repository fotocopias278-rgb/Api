"""Ingesta y análisis de comprobantes bancarios."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.exceptions import NotFoundError, ValidationError, VisionExtractionError
from app.core.security import sha256_hexdigest
from app.db.models import FraudAssessment, Merchant, Payment, Receipt
from app.schemas.enums import (
    PaymentMethod,
    PaymentStatus,
    ReceiptStatus,
    ReconciliationStatus,
    RiskDecision,
)
from app.schemas.receipt import ReconciliationResult
from app.schemas.vision import ReceiptExtraction
from app.services.fraud import FraudEngine, fraud_engine
from app.services.reconciliation import reconcile
from app.services.storage import ReceiptStorage, get_receipt_storage
from app.services.vision import SUPPORTED_MEDIA_TYPES, VisionProvider, get_vision_provider

logger = logging.getLogger(__name__)

# Estados sobre los que la conciliación puede actuar. Un pago ya liquidado,
# devuelto o rechazado no se toca: llegar tarde con un comprobante no puede
# deshacer una decisión ya tomada.
_ESTADOS_AFECTABLES = frozenset(
    {PaymentStatus.PENDING, PaymentStatus.PROCESSING, PaymentStatus.UNDER_REVIEW}
)


class ReceiptService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        merchant: Merchant,
        provider: VisionProvider | None = None,
        storage: ReceiptStorage | None = None,
        engine: FraudEngine | None = None,
        config: Settings | None = None,
    ) -> None:
        self._session = session
        self._merchant = merchant
        self._settings = config or default_settings
        self._provider = provider or get_vision_provider(self._settings)
        self._storage = storage or get_receipt_storage(self._settings)
        self._engine = engine or fraud_engine

    async def register(
        self,
        *,
        content: bytes,
        filename: str,
        content_type: str,
        payment_reference: str | None = None,
    ) -> tuple[Receipt, Receipt | None]:
        """Guarda el comprobante y lo deja listo para analizar.

        Devuelve `(comprobante, duplicado_previo)`. El duplicado no bloquea la
        ingesta: se registra igualmente para dejar traza del intento.
        """
        if content_type not in SUPPORTED_MEDIA_TYPES:
            raise ValidationError(
                f"Tipo de fichero no soportado: {content_type}",
                details={"supported": sorted(SUPPORTED_MEDIA_TYPES)},
            )
        if not content:
            raise ValidationError("El fichero está vacío.")
        if len(content) > self._settings.receipt_max_bytes:
            raise ValidationError(
                "El fichero supera el tamaño máximo permitido.",
                details={"max_bytes": self._settings.receipt_max_bytes},
            )

        digest = sha256_hexdigest(content)
        # Acotado al comercio: avisar de que "este fichero ya se subió" cuando
        # lo subió otro cliente revelaría que existe, y además son negocios
        # distintos — un mismo justificante puede llegar legítimamente a dos.
        previous = await self._session.scalar(
            select(Receipt)
            .where(Receipt.merchant_id == self._merchant.id, Receipt.content_sha256 == digest)
            .limit(1)
        )

        payment: Payment | None = None
        if payment_reference:
            payment = await self._session.scalar(
                select(Payment).where(
                    Payment.merchant_id == self._merchant.id,
                    Payment.reference == payment_reference,
                )
            )
            if payment is None:
                raise NotFoundError(f"No existe el pago {payment_reference}.")

        receipt = Receipt(
            merchant_id=self._merchant.id,
            payment_id=payment.id if payment else None,
            filename=filename[:255],
            content_type=content_type,
            size_bytes=len(content),
            content_sha256=digest,
            status=ReceiptStatus.PENDING,
        )
        self._session.add(receipt)
        # Se vuelca primero para disponer del id, que da nombre al fichero.
        await self._session.flush()
        receipt.storage_uri = await self._storage.save(
            f"{receipt.id}/{receipt.filename}", content
        )
        await self._session.commit()
        await self._session.refresh(receipt)
        return receipt, previous

    async def analyze(self, receipt_id: uuid.UUID) -> Receipt:
        """Extrae los datos del comprobante y lo concilia con su pago."""
        receipt = await self._get_own(receipt_id)

        if receipt.storage_uri is None:  # pragma: no cover - inconsistencia de datos
            raise NotFoundError(f"El comprobante {receipt_id} no tiene fichero asociado.")

        receipt.status = ReceiptStatus.PROCESSING
        await self._session.commit()

        content = await self._storage.load(receipt.storage_uri)
        try:
            extraction = await self._provider.extract(content, receipt.content_type)
        except VisionExtractionError as exc:
            receipt.status = ReceiptStatus.FAILED
            receipt.error_message = exc.message[:512]
            await self._session.commit()
            await self._session.refresh(receipt)
            logger.warning(
                "receipt.extraction_failed",
                extra={"receipt_id": str(receipt_id), "error": exc.message},
            )
            return receipt

        receipt.status = ReceiptStatus.EXTRACTED
        receipt.extraction = extraction.model_dump(mode="json")
        receipt.confidence = extraction.confidence
        await self._apply_reconciliation(receipt, extraction)

        await self._session.commit()
        await self._session.refresh(receipt)
        return receipt

    async def _apply_reconciliation(self, receipt: Receipt, extraction: ReceiptExtraction) -> None:
        """Concilia contra el pago asociado, si lo hay."""
        if receipt.payment_id is None:
            receipt.reconciliation_status = ReconciliationStatus.PENDING
            receipt.reconciliation_notes = ["Comprobante sin pago asociado."]
            return

        payment = await self._session.get(Payment, receipt.payment_id)
        if payment is None:  # pragma: no cover - protegido por clave foránea
            receipt.reconciliation_status = ReconciliationStatus.PENDING
            receipt.reconciliation_notes = ["El pago asociado ya no existe."]
            return

        duplicate = await self._operation_already_used(receipt, extraction)
        result = reconcile(
            extraction, payment, duplicate_operation=duplicate, config=self._settings
        )
        receipt.reconciliation_status = result.status
        receipt.reconciliation_notes = result.notes

        self._apply_to_payment(payment, result, extraction)

    def _apply_to_payment(
        self, payment: Payment, result: ReconciliationResult, extraction: ReceiptExtraction
    ) -> None:
        """Traslada el veredicto de la conciliación al pago.

        Es el paso que convierte la conciliación en algo accionable: sin esto el
        sistema solo emite un dictamen que nadie aplica.
        """
        if payment.status not in _ESTADOS_AFECTABLES:
            logger.info(
                "reconciliation.payment_no_afectable",
                extra={"reference": payment.reference, "status": payment.status},
            )
            return

        if result.status is ReconciliationStatus.UNREADABLE:
            # Nada que concluir: el documento requiere que alguien lo mire.
            return

        if result.status is ReconciliationStatus.MATCHED:
            self._on_matched(payment)
            return

        self._flag_payment(payment, result, extraction)

    def _on_matched(self, payment: Payment) -> None:
        # Solo una transferencia se liquida con el comprobante: en una tarjeta
        # quien manda es la pasarela, no el papel que suba el cliente.
        liquidable = (
            self._settings.reconciliation_auto_settle
            and payment.method is PaymentMethod.BANK_TRANSFER
        )
        if liquidable:
            payment.status = PaymentStatus.SUCCEEDED
            payment.settled_at = datetime.now(UTC)
            logger.info("reconciliation.payment_settled", extra={"reference": payment.reference})
            return

        payment.status = PaymentStatus.UNDER_REVIEW

    def _flag_payment(
        self, payment: Payment, result: ReconciliationResult, extraction: ReceiptExtraction
    ) -> None:
        """Registra las señales de fraude del comprobante y retiene el pago."""
        codes: list[str] = []
        if result.status is ReconciliationStatus.DUPLICATE:
            codes.append("RECEIPT_DUPLICATE")
        if extraction.tampering_indicators:
            codes.append("RECEIPT_TAMPERING")
        if result.status is ReconciliationStatus.MISMATCHED:
            codes.append("RECEIPT_MISMATCH")

        assessment = self._engine.evaluate_signals(codes)
        payment.assessments.append(FraudAssessment.from_evaluation(assessment))

        # Nunca se rechaza solo por el comprobante, ni con la puntuación al
        # máximo: la evidencia sale de un OCR y la última palabra es de una
        # persona. La puntuación queda registrada para que pueda juzgarla.
        payment.status = PaymentStatus.UNDER_REVIEW
        logger.info(
            "reconciliation.payment_flagged",
            extra={
                "reference": payment.reference,
                "score": assessment.score,
                "signals": [signal.code for signal in assessment.signals],
                "propuesta": RiskDecision.REJECT
                if assessment.decision is RiskDecision.REJECT
                else RiskDecision.REVIEW,
            },
        )

    async def _operation_already_used(
        self, receipt: Receipt, extraction: ReceiptExtraction
    ) -> bool:
        """¿Se usó ya ese número de operación para justificar otro pago?"""
        if not extraction.operation_number:
            return False

        rows = await self._session.execute(
            select(Receipt).where(
                Receipt.merchant_id == self._merchant.id,
                Receipt.id != receipt.id,
                Receipt.status == ReceiptStatus.EXTRACTED,
                Receipt.payment_id.is_not(None),
            )
        )
        for other in rows.scalars():
            other_op = (other.extraction or {}).get("operation_number")
            if (
                other_op == extraction.operation_number
                and other.payment_id != receipt.payment_id
            ):
                return True
        return False

    async def _get_own(self, receipt_id: uuid.UUID) -> Receipt:
        """Carga un comprobante propio, o 404 si es de otro comercio."""
        receipt = await self._session.scalar(
            select(Receipt).where(
                Receipt.merchant_id == self._merchant.id, Receipt.id == receipt_id
            )
        )
        if receipt is None:
            raise NotFoundError(f"No existe el comprobante {receipt_id}.")
        return receipt

    async def get(self, receipt_id: uuid.UUID) -> Receipt:
        return await self._get_own(receipt_id)
