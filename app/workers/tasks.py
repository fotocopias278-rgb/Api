"""Tareas asíncronas: extracción y conciliación de comprobantes."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from celery.exceptions import MaxRetriesExceededError
from celery.signals import worker_process_shutdown
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.db.models import Merchant, Payment, Receipt, WebhookEvent
from app.db.models.endpoint import WebhookDelivery, WebhookEndpoint
from app.db.session import SessionFactory, engine
from app.schemas.enums import ReceiptStatus
from app.services.notifications import WebhookDeliveryFailure, deliver_persisted_webhook
from app.services.receipts import ReceiptService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
_worker_loop: asyncio.AbstractEventLoop | None = None
_MAX_FAILED_RECEIPT_REQUEUES = 3


def _run_async(coroutine: Any) -> Any:
    """Reuse one event loop per worker process for SQLAlchemy's async pool."""
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop.run_until_complete(coroutine)


@worker_process_shutdown.connect
def _close_worker_loop(**_: Any) -> None:
    if _worker_loop is None or _worker_loop.is_closed():
        return
    _worker_loop.run_until_complete(engine.dispose())
    _worker_loop.close()
    asyncio.set_event_loop(None)


@celery_app.task(
    name="receipts.analyze",
    bind=True,
    ignore_result=True,
    max_retries=3,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
)
def analyze_receipt_task(self: Any, receipt_id: str) -> dict[str, Any]:
    """Ejecuta la extracción con IA y la conciliación de un comprobante."""
    try:
        receipt = _run_async(_analyze(uuid.UUID(receipt_id)))
    except NotFoundError:
        # Reintentar no lo hará aparecer: se registra y se abandona.
        logger.error("task.receipt_not_found", extra={"receipt_id": receipt_id})
        raise
    except MaxRetriesExceededError:  # pragma: no cover - camino de agotamiento
        logger.error("task.retries_exhausted", extra={"receipt_id": receipt_id})
        raise

    return {
        "receipt_id": receipt_id,
        "status": receipt.status,
        "reconciliation_status": receipt.reconciliation_status,
    }


async def _analyze(receipt_id: uuid.UUID):
    async with SessionFactory() as session:
        # El worker no tiene petición de la que sacar la clave: el inquilino se
        # deriva del propio comprobante, que ya lo lleva grabado.
        receipt = await session.get(Receipt, receipt_id)
        if receipt is None:
            raise NotFoundError(f"No existe el comprobante {receipt_id}.")
        merchant = await session.get(Merchant, receipt.merchant_id)
        if merchant is None:  # pragma: no cover - protegido por clave foránea
            raise NotFoundError(f"No existe el comercio {receipt.merchant_id}.")
        return await ReceiptService(session, merchant=merchant).analyze(receipt_id)


async def analyze_receipt_inline(receipt_id: uuid.UUID) -> None:
    """Analiza el comprobante en el propio proceso web, tras responder.

    Alternativa a la cola para despliegues de un solo servicio. Nunca propaga:
    esto corre después de que la respuesta haya salido, así que una excepción
    aquí no llegaría al cliente y solo ensuciaría el log del servidor.
    """
    try:
        await _analyze(receipt_id)
    except Exception as exc:
        logger.error(
            "receipt.inline_analysis_failed",
            extra={"receipt_id": str(receipt_id), "error": str(exc)},
            exc_info=True,
        )


@celery_app.task(name="receipts.requeue_stale")
def requeue_stale_receipts_task() -> int:
    """Recupera trabajos pendientes si el broker o un worker estuvo caído."""
    return _run_async(_requeue_stale_receipts())


async def _requeue_stale_receipts() -> int:
    now = datetime.now(UTC)
    retry_after = now - timedelta(minutes=20)
    pending_cutoff = now - timedelta(minutes=2)
    async with SessionFactory() as session:
        due = await session.scalars(
            select(Receipt)
            .where(
                or_(
                    and_(
                        Receipt.status == ReceiptStatus.PENDING,
                        Receipt.created_at <= pending_cutoff,
                    ),
                    and_(
                        Receipt.status == ReceiptStatus.PROCESSING,
                        Receipt.updated_at <= retry_after,
                    ),
                    and_(
                        Receipt.status == ReceiptStatus.FAILED,
                        Receipt.updated_at <= retry_after,
                        Receipt.requeue_attempts < _MAX_FAILED_RECEIPT_REQUEUES,
                    ),
                ),
                or_(Receipt.last_enqueued_at.is_(None), Receipt.last_enqueued_at <= retry_after),
            )
            .order_by(Receipt.created_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        receipts = list(due)
        for receipt in receipts:
            receipt.last_enqueued_at = now
            if receipt.status is ReceiptStatus.FAILED:
                receipt.requeue_attempts += 1
        await session.commit()
        receipt_ids = [receipt.id for receipt in receipts]

    enqueued = 0
    for receipt_id in receipt_ids:
        try:
            analyze_receipt_task.delay(str(receipt_id))
            enqueued += 1
        except Exception as exc:
            logger.error(
                "task.receipt_requeue_failed",
                extra={"receipt_id": str(receipt_id), "error": str(exc)},
                exc_info=True,
            )
    return enqueued


@celery_app.task(
    name="webhooks.deliver",
    bind=True,
    max_retries=5,
    autoretry_for=(WebhookDeliveryFailure,),
    retry_backoff=True,
    retry_jitter=True,
)
def deliver_webhook_task(self: Any, delivery_id: str) -> None:
    _run_async(deliver_persisted_webhook(uuid.UUID(delivery_id)))


@celery_app.task(name="webhooks.requeue_stale")
def requeue_stale_webhooks_task() -> int:
    return _run_async(_requeue_stale_webhooks())


@celery_app.task(name="privacy.anonymize_expired")
def anonymize_expired_payment_data_task() -> int:
    """Elimina datos personales de pagos que superan su periodo de retención."""
    return _run_async(_anonymize_expired_payment_data(settings.data_retention_days))


async def _anonymize_expired_payment_data(retention_days: int) -> int:
    async with SessionFactory() as session:
        return await _anonymize_expired_payment_data_in_session(
            session, retention_days
        )


async def _anonymize_expired_payment_data_in_session(
    session: AsyncSession, retention_days: int, *, now: datetime | None = None
) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    result = await session.execute(
        update(Payment)
        .where(
            Payment.created_at < cutoff,
            or_(Payment.email.is_not(None), Payment.ip_address.is_not(None)),
        )
        .values(email=None, ip_address=None)
    )
    payment_count = result.rowcount or 0
    await session.execute(
        delete(WebhookDelivery).where(
            WebhookDelivery.created_at < cutoff,
            WebhookDelivery.delivered_at.is_not(None),
        )
    )
    deliveries = await session.stream_scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.created_at < cutoff)
        .execution_options(yield_per=500)
    )
    delivery_count = 0
    async for delivery in deliveries:
        payload = _anonymize_payload(delivery.payload)
        if payload != delivery.payload:
            delivery.payload = payload
            delivery_count += 1
    events = await session.stream_scalars(
        select(WebhookEvent)
        .where(WebhookEvent.created_at < cutoff)
        .execution_options(yield_per=500)
    )
    event_count = 0
    async for event in events:
        payload = _anonymize_payload(event.payload)
        if payload != event.payload:
            event.payload = payload
            event_count += 1
    await session.commit()
    logger.info(
        "privacy.expired_payment_data_anonymized",
        extra={
            "payments": payment_count,
            "webhook_deliveries": delivery_count,
            "webhook_events": event_count,
            "retention_days": retention_days,
        },
    )
    return payment_count


def _anonymize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: None if key in {"email", "ip", "ip_address"} else _anonymize_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_anonymize_payload(item) for item in value]
    return value


async def _requeue_stale_webhooks() -> int:
    now = datetime.now(UTC)
    retry_after = now - timedelta(minutes=10)
    async with SessionFactory() as session:
        due = await session.scalars(
            select(WebhookDelivery)
            .join(WebhookEndpoint, WebhookDelivery.endpoint_id == WebhookEndpoint.id)
            .where(
                WebhookEndpoint.is_active.is_(True),
                WebhookDelivery.delivered_at.is_(None),
                WebhookDelivery.attempts < 12,
                or_(
                    WebhookDelivery.last_error.is_(None),
                    ~WebhookDelivery.last_error.like("Destino de webhook rechazado:%"),
                ),
                or_(
                    WebhookDelivery.last_enqueued_at.is_(None),
                    WebhookDelivery.last_enqueued_at <= retry_after,
                ),
            )
            .order_by(WebhookDelivery.created_at)
            .limit(100)
            .with_for_update(skip_locked=True, of=WebhookDelivery)
        )
        deliveries = list(due)
        for delivery in deliveries:
            delivery.last_enqueued_at = now
        await session.commit()
        delivery_ids = [delivery.id for delivery in deliveries]

    enqueued = 0
    for delivery_id in delivery_ids:
        try:
            deliver_webhook_task.delay(str(delivery_id))
            enqueued += 1
        except Exception as exc:
            logger.error(
                "webhook.requeue_failed",
                extra={"delivery_id": str(delivery_id), "error": str(exc)},
                exc_info=True,
            )
    return enqueued


def enqueue_receipt_analysis(receipt_id: uuid.UUID) -> str | None:
    """Encola el análisis y devuelve el id de tarea.

    Si el broker no está disponible no se pierde la petición: el comprobante
    queda en `pending` y puede reprocesarse; la API responde igualmente 202.
    """
    try:
        result = analyze_receipt_task.delay(str(receipt_id))
    except Exception as exc:
        # Captura deliberadamente amplia: un broker caído se manifiesta de
        # muchas formas (OperationalError de kombu, ConnectionError de redis,
        # RuntimeError del backend de resultados). El comprobante ya está
        # guardado, así que se responde 202 y queda para reprocesar; perder la
        # subida por una caída de la cola sería mucho peor.
        logger.error(
            "task.enqueue_failed",
            extra={"receipt_id": str(receipt_id), "error": str(exc)},
            exc_info=True,
        )
        return None
    return result.id
