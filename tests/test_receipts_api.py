"""Ingesta, extracción y conciliación de comprobantes."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.exceptions import VisionExtractionError
from app.db.models import Payment, Receipt
from app.schemas.enums import PaymentStatus, ReceiptStatus, ReconciliationStatus
from app.schemas.vision import DocumentType, ReceiptExtraction
from app.services.receipts import ReceiptService
from tests.conftest import AUTH_HEADERS

# PNG mínimo válido de 1x1 px.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


class FakeVisionProvider:
    """Proveedor de visión controlado por el test."""

    def __init__(self, extraction: ReceiptExtraction | None = None, error: Exception | None = None):
        self.extraction = extraction
        self.error = error
        self.calls = 0

    async def extract(self, content: bytes, media_type: str) -> ReceiptExtraction:
        self.calls += 1
        if self.error:
            raise self.error
        assert self.extraction is not None
        return self.extraction


def extraccion(**overrides) -> ReceiptExtraction:
    base = {
        "document_type": DocumentType.TRANSFER_RECEIPT,
        "is_legible": True,
        "bank_name": "Banco de Prueba",
        "operation_number": "OP-777",
        "amount": "25.00",
        "currency": "EUR",
        "transaction_date": datetime.now(UTC).isoformat(),
        "sender_name": None,
        "sender_account": None,
        "receiver_name": None,
        "receiver_account": None,
        "concept": None,
        "tampering_indicators": [],
        "confidence": 0.91,
    }
    return ReceiptExtraction(**{**base, **overrides})


async def crear_pago(client, **overrides) -> str:
    body = {"customer_id": "cliente-1", "amount_minor": 2_500, "currency": "EUR", **overrides}
    response = await client.post("/api/v1/payments", json=body, headers=AUTH_HEADERS)
    return response.json()["reference"]


async def subir(client, contenido=PNG_1PX, nombre="recibo.png", tipo="image/png", referencia=None):
    files = {"file": (nombre, contenido, tipo)}
    data = {"payment_reference": referencia} if referencia else None
    return await client.post("/api/v1/receipts", files=files, data=data, headers=AUTH_HEADERS)


# --- Ingesta -----------------------------------------------------------------

async def test_subida_devuelve_202_y_queda_pendiente(client):
    response = await subir(client)
    assert response.status_code == 202

    body = response.json()
    assert body["status"] == ReceiptStatus.PENDING
    assert body["task_id"] == "task-simulada"
    assert body["duplicate_of"] is None


async def test_subida_asociada_a_un_pago(client):
    referencia = await crear_pago(client)
    response = await subir(client, referencia=referencia)
    assert response.status_code == 202

    detalle = await client.get(f"/api/v1/receipts/{response.json()['id']}", headers=AUTH_HEADERS)
    assert detalle.json()["payment_reference"] == referencia


async def test_se_detecta_el_mismo_fichero_subido_dos_veces(client):
    primera = await subir(client)
    segunda = await subir(client)
    assert segunda.json()["duplicate_of"] == primera.json()["id"]


async def test_tipo_de_fichero_no_soportado(client):
    response = await subir(client, contenido=b"texto", nombre="nota.txt", tipo="text/plain")
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_fichero_vacio(client):
    response = await subir(client, contenido=b"")
    assert response.status_code == 422


async def test_comprobante_para_un_pago_inexistente(client):
    response = await subir(client, referencia="pay_no_existe")
    assert response.status_code == 404


async def test_consultar_comprobante_inexistente(client):
    response = await client.get(f"/api/v1/receipts/{uuid.uuid4()}", headers=AUTH_HEADERS)
    assert response.status_code == 404


# --- Análisis ----------------------------------------------------------------

async def test_analisis_extrae_y_concilia(client, session, settings):
    referencia = await crear_pago(client)
    receipt_id = uuid.UUID((await subir(client, referencia=referencia)).json()["id"])

    service = ReceiptService(
        session, provider=FakeVisionProvider(extraccion()), config=settings
    )
    receipt = await service.analyze(receipt_id)

    assert receipt.status is ReceiptStatus.EXTRACTED
    assert receipt.confidence == pytest.approx(0.91)
    assert receipt.extraction["bank_name"] == "Banco de Prueba"
    assert receipt.reconciliation_status is ReconciliationStatus.MATCHED


async def test_importe_que_no_cuadra_marca_discrepancia(client, session, settings):
    referencia = await crear_pago(client)
    receipt_id = uuid.UUID((await subir(client, referencia=referencia)).json()["id"])

    service = ReceiptService(
        session, provider=FakeVisionProvider(extraccion(amount="999.00")), config=settings
    )
    receipt = await service.analyze(receipt_id)

    assert receipt.reconciliation_status is ReconciliationStatus.MISMATCHED
    assert receipt.reconciliation_notes


async def test_comprobante_sin_pago_queda_pendiente_de_conciliar(client, session, settings):
    receipt_id = uuid.UUID((await subir(client)).json()["id"])

    service = ReceiptService(session, provider=FakeVisionProvider(extraccion()), config=settings)
    receipt = await service.analyze(receipt_id)

    assert receipt.status is ReceiptStatus.EXTRACTED
    assert receipt.reconciliation_status is ReconciliationStatus.PENDING


async def test_fallo_del_modelo_deja_el_comprobante_como_fallido(client, session, settings):
    receipt_id = uuid.UUID((await subir(client)).json()["id"])

    service = ReceiptService(
        session,
        provider=FakeVisionProvider(error=VisionExtractionError("el modelo no respondió")),
        config=settings,
    )
    receipt = await service.analyze(receipt_id)

    assert receipt.status is ReceiptStatus.FAILED
    assert "no respondió" in receipt.error_message
    # Un fallo de extracción nunca da por buena la conciliación.
    assert receipt.reconciliation_status is ReconciliationStatus.PENDING


async def test_justificante_reutilizado_en_otro_pago_es_duplicado(client, session, settings):
    """El mismo número de operación no puede justificar dos pagos distintos."""
    primera_ref = await crear_pago(client, customer_id="cliente-a")
    segunda_ref = await crear_pago(client, customer_id="cliente-b")

    provider = FakeVisionProvider(extraccion())
    service = ReceiptService(session, provider=provider, config=settings)

    primer_id = uuid.UUID((await subir(client, referencia=primera_ref)).json()["id"])
    primero = await service.analyze(primer_id)
    assert primero.reconciliation_status is ReconciliationStatus.MATCHED

    # Fichero distinto (para no chocar con el hash) pero mismo nº de operación.
    segundo_id = uuid.UUID(
        (await subir(client, contenido=PNG_1PX + b"\x00", referencia=segunda_ref)).json()["id"]
    )
    segundo = await service.analyze(segundo_id)

    assert segundo.reconciliation_status is ReconciliationStatus.DUPLICATE


async def test_el_contenido_se_guarda_y_se_recupera(client, session, settings):
    """El worker debe poder releer el fichero que subió la API."""
    receipt_id = uuid.UUID((await subir(client)).json()["id"])
    receipt = await session.scalar(select(Receipt).where(Receipt.id == receipt_id))

    assert receipt.storage_uri is not None
    assert receipt.size_bytes == len(PNG_1PX)

    provider = FakeVisionProvider(extraccion())
    await ReceiptService(session, provider=provider, config=settings).analyze(receipt_id)
    assert provider.calls == 1


async def test_el_proveedor_por_defecto_no_da_nada_por_bueno(client, session, settings):
    """Sin credenciales de IA, la conciliación queda para revisión manual."""
    referencia = await crear_pago(client)
    receipt_id = uuid.UUID((await subir(client, referencia=referencia)).json()["id"])

    receipt = await ReceiptService(session, config=settings).analyze(receipt_id)

    assert receipt.reconciliation_status is ReconciliationStatus.UNREADABLE
    payment = await session.scalar(select(Payment).where(Payment.reference == referencia))
    assert payment.status is not PaymentStatus.SUCCEEDED
