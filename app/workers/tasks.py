"""Tareas asíncronas: extracción y conciliación de comprobantes."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from celery.exceptions import MaxRetriesExceededError

from app.core.exceptions import NotFoundError
from app.db.session import SessionFactory
from app.services.receipts import ReceiptService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


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
        receipt = asyncio.run(_analyze(uuid.UUID(receipt_id)))
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
        return await ReceiptService(session).analyze(receipt_id)


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
