"""Tareas asíncronas: extracción y conciliación de comprobantes."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from celery.exceptions import MaxRetriesExceededError

from app.core.exceptions import NotFoundError
from app.db.models import Merchant, Receipt
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
