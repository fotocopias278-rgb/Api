"""Orquestación del ciclo de vida de un pago."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.exceptions import ConflictError, GatewayError, NotFoundError, ValidationError
from app.db.models import FraudAssessment, Merchant, Payment, RefundOperation
from app.db.models.endpoint import EventType
from app.db.models.payment import new_reference
from app.db.models.usage import UsageKind
from app.schemas.enums import PaymentMethod, PaymentStatus, RiskDecision
from app.schemas.payment import PaymentCreate
from app.services.fraud import FraudContext, FraudEngine, fraud_engine
from app.services.gateway import PaymentGatewayProtocol
from app.services.mercadopago_gateway import MercadoPagoGateway, mercadopago_gateway
from app.services.notifications import NotificationService
from app.services.stripe_gateway import StripeGateway, stripe_gateway
from app.services.usage import UsageService

logger = logging.getLogger(__name__)

# Estados que cuentan como intento fallido al medir el riesgo del cliente.
_FAILED_STATUSES = (PaymentStatus.FAILED, PaymentStatus.REJECTED)


class PaymentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        merchant: Merchant,
        engine: FraudEngine | None = None,
        gateway: PaymentGatewayProtocol | None = None,
        config: Settings | None = None,
    ) -> None:
        self._session = session
        # El comercio es obligatorio y no tiene valor por defecto: así una
        # consulta sin inquilino falla al construir el servicio, no en
        # producción con los datos de otro cliente ya servidos.
        self._merchant = merchant
        self._engine = engine or fraud_engine
        self._gateway_override = gateway
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
            ).where(
                Payment.merchant_id == self._merchant.id,
                Payment.customer_id == data.customer_id,
            )
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
        request_hash = self._request_hash(data)
        if idempotency_key:
            existing = await self.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                self._validate_idempotent_replay(existing, request_hash)
                return existing

        assessment = self._engine.evaluate(await self.build_fraud_context(data))

        payment = Payment(
            merchant_id=self._merchant.id,
            reference=new_reference(),
            customer_id=data.customer_id,
            amount_minor=data.amount_minor,
            currency=data.currency,
            provider=data.provider,
            method=data.method,
            email=data.email,
            ip_address=data.ip_address,
            card_bin=data.card_bin,
            card_country=data.card_country,
            ip_country=data.ip_country,
            idempotency_key=idempotency_key,
            idempotency_payload_hash=request_hash if idempotency_key else None,
            risk_score=assessment.score,
            risk_decision=assessment.decision,
            status=self._initial_status(assessment.decision),
            meta=data.meta,
        )
        payment.assessments.append(FraudAssessment.from_evaluation(assessment))
        self._session.add(payment)
        # El hecho facturable viaja en la misma transacción que el pago: o se
        # guardan los dos, o ninguno. Facturar algo que no ocurrió, o dejar de
        # facturar algo que sí, son igual de malos.
        await UsageService(self._session).record(
            self._merchant.id,
            UsageKind.PAYMENT_CREATED,
            meta={"currency": data.currency},
            commit=False,
        )

        # Se persiste y se confirma ANTES de hablar con la pasarela, por dos
        # razones: no mantener una transacción abierta durante una llamada de
        # red (agota el pool bajo carga), y liberar cuanto antes el bloqueo
        # sobre el índice único de la clave de idempotencia.
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            duplicado = await self._resolve_idempotency_conflict(idempotency_key, request_hash)
            if duplicado is None:
                raise
            return duplicado

        await self._session.refresh(payment)

        # Una transferencia no se cobra por la pasarela: el cliente envía el
        # dinero por su banco y lo que la liquida es el comprobante conciliado.
        # Crear aquí un PaymentIntent abriría un cobro con tarjeta paralelo.
        if assessment.decision is RiskDecision.APPROVE and payment.method is PaymentMethod.CARD:
            await self._authorize(payment)
            await self._session.commit()
            await self._session.refresh(payment)

        await self._avisar(payment)
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

    async def _avisar(self, payment: Payment) -> None:
        """Emite `payment.updated` hacia los destinos del comercio."""
        await NotificationService(self._session, config=self._settings).emit(
            self._merchant.id,
            EventType.PAYMENT_UPDATED,
            {
                "reference": payment.reference,
                "status": str(payment.status),
                "amount_minor": payment.amount_minor,
                "currency": payment.currency,
                "risk_score": payment.risk_score,
                "risk_decision": str(payment.risk_decision) if payment.risk_decision else None,
            },
        )

    async def _authorize(self, payment: Payment) -> None:
        """Envía el cobro a la pasarela y refleja el resultado en el pago."""
        try:
            charge = await self._gateway_for(payment.provider).create_payment_intent(
                amount_minor=payment.amount_minor,
                currency=payment.currency,
                reference=payment.reference,
                customer_id=payment.customer_id,
                idempotency_key=payment.idempotency_key,
                metadata={"merchant_id": str(payment.merchant_id)},
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
        self,
        reference: str,
        *,
        amount_minor: int | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> Payment:
        payment = await self._session.scalar(
            select(Payment)
            .where(
                Payment.merchant_id == self._merchant.id,
                Payment.reference == reference,
            )
            .with_for_update()
        )
        if payment is None:
            raise NotFoundError(f"No existe el pago {reference}.")
        if not idempotency_key:
            if payment.status not in {
                PaymentStatus.SUCCEEDED,
                PaymentStatus.PARTIALLY_REFUNDED,
            }:
                raise ConflictError(
                    f"Solo se pueden devolver pagos cobrados; estado actual: {payment.status}.",
                    details={"reference": reference, "status": payment.status},
                )
            raise ValidationError("La devolución requiere la cabecera Idempotency-Key.")

        operation = await self._session.scalar(
            select(RefundOperation).where(
                RefundOperation.merchant_id == self._merchant.id,
                RefundOperation.idempotency_key == idempotency_key,
            )
        )
        if operation is not None:
            if operation.payment_id != payment.id:
                raise ConflictError("La clave de idempotencia ya se usó para otro pago.")
            if operation.requested_amount_minor != amount_minor:
                raise ConflictError("La clave de idempotencia ya se usó con otro importe.")
            if reason != operation.reason:
                raise ConflictError("La clave de idempotencia ya se usó con otro motivo.")
            if operation.status == "succeeded":
                return payment
            if operation.status == "failed":
                raise ConflictError(
                    "Esta devolución falló definitivamente; usa otra Idempotency-Key."
                )
        else:
            if payment.status not in {
                PaymentStatus.SUCCEEDED,
                PaymentStatus.PARTIALLY_REFUNDED,
            }:
                raise ConflictError(
                    f"Solo se pueden devolver pagos cobrados; estado actual: {payment.status}.",
                    details={"reference": reference, "status": payment.status},
                )
            reservado = await self._session.scalar(
                select(func.coalesce(func.sum(RefundOperation.amount_minor), 0)).where(
                    RefundOperation.payment_id == payment.id,
                    RefundOperation.status == "pending",
                )
            )
            restante = payment.amount_minor - payment.refunded_amount_minor - int(reservado or 0)
            importe = amount_minor if amount_minor is not None else restante
            if importe <= 0 or importe > restante:
                raise ConflictError("El importe a devolver supera el importe aún disponible.")
            if payment.provider_payment_id is None:
                raise ConflictError("El pago no tiene identificador en la pasarela.")
            operation = RefundOperation(
                merchant_id=self._merchant.id,
                payment_id=payment.id,
                idempotency_key=idempotency_key,
                requested_amount_minor=amount_minor,
                amount_minor=importe,
                reason=reason,
                status="pending",
            )
            self._session.add(operation)
            try:
                await self._session.commit()
            except IntegrityError:
                await self._session.rollback()
                payment = await self._session.scalar(
                    select(Payment)
                    .where(
                        Payment.merchant_id == self._merchant.id,
                        Payment.reference == reference,
                    )
                    .with_for_update()
                )
                operation = await self._session.scalar(
                    select(RefundOperation).where(
                        RefundOperation.merchant_id == self._merchant.id,
                        RefundOperation.idempotency_key == idempotency_key,
                    )
                )
                if payment is None or operation is None:
                    raise
                if (
                    operation.payment_id != payment.id
                    or operation.requested_amount_minor != amount_minor
                    or operation.reason != reason
                ):
                    raise ConflictError(
                        "La clave de idempotencia ya se usó con otra devolución."
                    ) from None
                if operation.status == "succeeded":
                    return payment
            await self._session.refresh(payment)
            await self._session.refresh(operation)

        if payment.status not in {
            PaymentStatus.SUCCEEDED,
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.REFUNDED,
        }:
            raise ConflictError(
                f"Solo se pueden devolver pagos cobrados; estado actual: {payment.status}.",
                details={"reference": reference, "status": payment.status},
            )
        if payment.provider_payment_id is None:
            raise ConflictError("El pago no tiene identificador en la pasarela.")

        try:
            resultado = await self._gateway_for(payment.provider).refund(
                provider_payment_id=payment.provider_payment_id,
                amount_minor=operation.amount_minor,
                currency=payment.currency,
                reason=operation.reason,
                idempotency_key=hashlib.sha256(
                    f"{self._merchant.id}:{idempotency_key}".encode()
                ).hexdigest(),
            )
        except GatewayError:
            # Se conserva `pending`: un timeout puede ocurrir tras aceptar el
            # reembolso. El reintento usa la misma clave idempotente del proveedor.
            raise

        if resultado.status in {"failed", "canceled", "cancelled", "rejected"}:
            operation.status = "failed"
            operation.provider_refund_id = str(resultado.raw.get("id") or "") or None
            await self._session.commit()
            raise GatewayError(
                "La pasarela rechazó definitivamente la devolución; usa otra "
                "Idempotency-Key si deseas iniciar un nuevo intento."
            )

        if resultado.status not in {"succeeded", "approved", "refunded"}:
            operation.provider_refund_id = str(resultado.raw.get("id") or "") or None
            await self._session.commit()
            raise GatewayError(
                "La devolución sigue pendiente en la pasarela; reintenta con la misma "
                "Idempotency-Key para consultar el mismo intento."
            )

        operation.status = "succeeded"
        operation.provider_refund_id = str(resultado.raw.get("id") or "") or None
        await self._session.flush()
        reembolsos_confirmados = await self._session.scalar(
            select(func.coalesce(func.sum(RefundOperation.amount_minor), 0)).where(
                RefundOperation.payment_id == payment.id,
                RefundOperation.status == "succeeded",
            )
        )
        payment.refunded_amount_minor = max(
            payment.refunded_amount_minor, int(reembolsos_confirmados or 0)
        )
        payment.status = (
            PaymentStatus.REFUNDED
            if payment.refunded_amount_minor >= payment.amount_minor
            else PaymentStatus.PARTIALLY_REFUNDED
        )
        await self._session.commit()
        await self._session.refresh(payment)
        await self._avisar(payment)
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
            if payment.method is PaymentMethod.BANK_TRANSFER:
                # En una transferencia el dinero ya llegó y el comprobante es la
                # prueba: crear un cobro en la pasarela cobraría por segunda vez.
                payment.status = PaymentStatus.SUCCEEDED
                payment.settled_at = datetime.now(UTC)
            else:
                await self._authorize(payment)
        else:
            payment.status = PaymentStatus.REJECTED
            payment.failure_reason = "Rechazado en revisión manual."

        await self._session.commit()
        await self._session.refresh(payment)
        await self._avisar(payment)
        return payment

    # --- Consultas ----------------------------------------------------------

    @staticmethod
    def _request_hash(data: PaymentCreate) -> str:
        canonical = json.dumps(
            data.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(canonical).hexdigest()

    def _validate_idempotent_replay(self, payment: Payment, request_hash: str) -> None:
        if payment.idempotency_payload_hash:
            existing_hash = payment.idempotency_payload_hash
        else:
            legacy_data = PaymentCreate(
                customer_id=payment.customer_id,
                amount_minor=payment.amount_minor,
                currency=payment.currency,
                method=payment.method,
                email=payment.email,
                ip_address=payment.ip_address,
                card_bin=payment.card_bin,
                card_country=payment.card_country,
                ip_country=payment.ip_country,
                meta=payment.meta,
            )
            existing_hash = self._request_hash(legacy_data)
        if existing_hash != request_hash:
            raise ConflictError(
                "La clave de idempotencia ya se usó con otra petición."
            )

    async def _resolve_idempotency_conflict(
        self, key: str | None, request_hash: str
    ) -> Payment | None:
        """Recupera el pago que ganó la carrera por la clave de idempotencia.

        Cuando dos peticiones simultáneas usan la misma clave, la perdedora
        choca contra el índice único. Para entonces la ganadora ya confirmó
        —el índice bloquea hasta el commit—, así que basta con releerlo y
        devolver ese mismo pago: es justo lo que pedía el cliente al reintentar.
        Si no aparece, el conflicto era otro y se propaga.
        """
        if key is None:
            return None
        payment = await self.get_by_idempotency_key(key)
        if payment is not None:
            self._validate_idempotent_replay(payment, request_hash)
        return payment

    async def get_by_reference(self, reference: str) -> Payment | None:
        """Un pago de otro comercio no existe: se devuelve None, no un 403.

        Decir "existe pero no es tuyo" filtra qué referencias están en uso.
        """
        result = await self._session.execute(
            select(Payment).where(
                Payment.merchant_id == self._merchant.id, Payment.reference == reference
            )
        )
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(self, key: str) -> Payment | None:
        result = await self._session.execute(
            select(Payment).where(
                Payment.merchant_id == self._merchant.id, Payment.idempotency_key == key
            )
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
        filters = [Payment.merchant_id == self._merchant.id]
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

    def _gateway_for(self, provider: str) -> PaymentGatewayProtocol:
        if self._gateway_override is not None:
            return self._gateway_override
        if provider == "mercadopago":
            if self._settings is default_settings:
                return mercadopago_gateway
            return MercadoPagoGateway(self._settings)
        if self._settings is default_settings:
            return stripe_gateway
        return StripeGateway(self._settings)
