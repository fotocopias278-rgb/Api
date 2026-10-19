"""Encolado de tareas en segundo plano."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from kombu.exceptions import OperationalError

from app.db.models import Receipt
from app.schemas.enums import ReceiptStatus
from app.workers import tasks
from tests.conftest import MERCHANT_ID


@pytest.mark.parametrize(
    "fallo",
    [
        OperationalError("broker inaccesible"),
        ConnectionError("Error 111 connecting to localhost:6379"),
        # El backend de resultados de Celery falla con RuntimeError, no con
        # una excepción de conexión: es la que tumbaba la subida.
        RuntimeError("Retry limit exceeded while trying to reconnect"),
    ],
)
def test_una_cola_caida_no_rompe_la_subida(monkeypatch, fallo):
    def delay_que_falla(_):
        raise fallo

    monkeypatch.setattr(tasks.analyze_receipt_task, "delay", delay_que_falla)

    # No propaga: el comprobante ya está guardado y puede reprocesarse.
    assert tasks.enqueue_receipt_analysis(uuid.uuid4()) is None


def test_el_encolado_devuelve_el_identificador(monkeypatch):
    class ResultadoFalso:
        id = "tarea-123"

    monkeypatch.setattr(tasks.analyze_receipt_task, "delay", lambda _: ResultadoFalso())
    assert tasks.enqueue_receipt_analysis(uuid.uuid4()) == "tarea-123"


def test_la_tarea_no_usa_backend_de_resultados():
    """El estado se consulta en la base de datos, no en Redis."""
    assert tasks.analyze_receipt_task.ignore_result is True


async def test_el_barrido_reintenta_failed_hasta_el_tope(
    session, session_factory, monkeypatch
):
    now = datetime.now(UTC)
    exhausted = Receipt(
        merchant_id=MERCHANT_ID,
        filename="exhausted.png",
        content_type="image/png",
        size_bytes=10,
        content_sha256="a" * 64,
        storage_uri="11111111-1111-1111-1111-111111111111/exhausted.png",
        status=ReceiptStatus.FAILED,
        requeue_attempts=3,
        last_enqueued_at=now - timedelta(hours=1),
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=1),
    )
    retryable = Receipt(
        merchant_id=MERCHANT_ID,
        filename="retryable.png",
        content_type="image/png",
        size_bytes=10,
        content_sha256="b" * 64,
        storage_uri="11111111-1111-1111-1111-111111111111/retryable.png",
        status=ReceiptStatus.FAILED,
        requeue_attempts=2,
        last_enqueued_at=now - timedelta(hours=1),
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=1),
    )
    session.add_all([exhausted, retryable])
    await session.commit()

    queued = []
    monkeypatch.setattr(tasks, "SessionFactory", session_factory)
    monkeypatch.setattr(tasks.analyze_receipt_task, "delay", lambda id_: queued.append(id_))

    assert await tasks._requeue_stale_receipts() == 1
    await session.refresh(exhausted)
    await session.refresh(retryable)
    assert queued == [str(retryable.id)]
    assert exhausted.requeue_attempts == 3
    assert retryable.requeue_attempts == 3
