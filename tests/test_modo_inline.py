"""Procesado del comprobante en el propio proceso web (despliegue de un servicio)."""

import uuid

from app.schemas.enums import ReceiptStatus, ReconciliationStatus
from app.services.receipts import ReceiptService
from app.workers import tasks
from tests.conftest import AUTH_HEADERS
from tests.test_receipts_api import PNG_1PX, FakeVisionProvider, extraccion


async def test_en_modo_inline_no_se_usa_la_cola(client, monkeypatch, settings):
    """No debe tocarse Celery: en este modo puede no haber broker siquiera."""
    monkeypatch.setattr(settings, "receipt_processing_mode", "inline")

    def no_debe_llamarse(_):
        raise AssertionError("no debería encolarse en modo inline")

    monkeypatch.setattr(
        "app.api.v1.endpoints.receipts.enqueue_receipt_analysis", no_debe_llamarse
    )
    programadas: list = []
    monkeypatch.setattr(
        "app.api.v1.endpoints.receipts.analyze_receipt_inline",
        lambda receipt_id: programadas.append(receipt_id),
    )

    response = await client.post(
        "/api/v1/receipts",
        files={"file": ("recibo.png", PNG_1PX, "image/png")},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 202
    assert response.json()["task_id"] is None


async def test_el_analisis_en_proceso_nunca_propaga(monkeypatch):
    """Corre después de responder: una excepción aquí no llegaría al cliente."""

    async def revienta(_):
        raise RuntimeError("la base de datos se cayó")

    monkeypatch.setattr(tasks, "_analyze", revienta)
    # No lanza: se registra y se sigue.
    await tasks.analyze_receipt_inline(uuid.uuid4())


async def test_el_modo_inline_produce_el_mismo_resultado(client, session, settings, merchant):
    """Cambiar de modo no debe cambiar lo que acaba guardado."""
    pago = await client.post(
        "/api/v1/payments",
        json={"customer_id": "cli_1", "amount_minor": 2_500, "currency": "EUR",
              "method": "bank_transfer"},
        headers=AUTH_HEADERS,
    )
    referencia = pago.json()["reference"]

    subida = await client.post(
        "/api/v1/receipts",
        files={"file": ("recibo.png", PNG_1PX, "image/png")},
        data={"payment_reference": referencia},
        headers=AUTH_HEADERS,
    )
    receipt_id = uuid.UUID(subida.json()["id"])

    receipt = await ReceiptService(session, merchant=merchant, provider=FakeVisionProvider(extraccion()), config=settings
    ).analyze(receipt_id)

    assert receipt.status is ReceiptStatus.EXTRACTED
    assert receipt.reconciliation_status is ReconciliationStatus.MATCHED
