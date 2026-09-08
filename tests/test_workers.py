"""Encolado de tareas en segundo plano."""

import uuid

import pytest
from kombu.exceptions import OperationalError

from app.workers import tasks


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
