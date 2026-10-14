"""El veredicto de la conciliación debe mover el pago, no quedarse en el papel."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Payment
from app.schemas.enums import PaymentStatus, ReconciliationStatus
from app.services.receipts import ReceiptService
from tests.conftest import AUTH_HEADERS
from tests.conftest import ajustes_con as ajustes
from tests.test_receipts_api import PNG_1PX, FakeVisionProvider, extraccion


async def crear_pago(client, method="bank_transfer", **overrides) -> str:
    cuerpo = {
        "customer_id": "cli_conc",
        "amount_minor": 2_500,
        "currency": "EUR",
        "method": method,
        **overrides,
    }
    response = await client.post("/api/v1/payments", json=cuerpo, headers=AUTH_HEADERS)
    return response.json()["reference"]


async def subir(client, referencia, contenido=PNG_1PX):
    files = {"file": ("recibo.png", contenido, "image/png")}
    response = await client.post(
        "/api/v1/receipts", files=files, data={"payment_reference": referencia},
        headers=AUTH_HEADERS,
    )
    return uuid.UUID(response.json()["id"])


async def pago(session, referencia) -> Payment:
    return await session.scalar(select(Payment).where(Payment.reference == referencia))


async def analizar(session, merchant, receipt_id, extraction, config=None):
    service = ReceiptService(
        session,
        merchant=merchant,
        provider=FakeVisionProvider(extraction),
        config=config or get_settings(),
    )
    return await service.analyze(receipt_id)


# --- Modo "proponer" (por defecto) ------------------------------------------

async def test_un_comprobante_que_cuadra_deja_el_pago_en_revision(client, session, merchant):
    """Por defecto no se liquida solo: se propone a una persona."""
    referencia = await crear_pago(client)
    receipt_id = await subir(client, referencia)

    receipt = await analizar(session, merchant, receipt_id, extraccion())
    assert receipt.reconciliation_status is ReconciliationStatus.MATCHED

    actualizado = await pago(session, referencia)
    await session.refresh(actualizado)
    assert actualizado.status is PaymentStatus.UNDER_REVIEW
    assert actualizado.settled_at is None


# --- Modo "liquidación automática" ------------------------------------------

async def test_con_liquidacion_automatica_la_transferencia_se_cobra(client, session, merchant):
    referencia = await crear_pago(client)
    receipt_id = await subir(client, referencia)

    await analizar(session, merchant, receipt_id, extraccion(), config=ajustes(reconciliation_auto_settle=True)
    )

    actualizado = await pago(session, referencia)
    await session.refresh(actualizado)
    assert actualizado.status is PaymentStatus.SUCCEEDED
    assert actualizado.settled_at is not None


async def test_una_tarjeta_no_se_liquida_con_un_comprobante(client, session, merchant):
    """En tarjeta manda la pasarela, no el papel que suba el cliente."""
    referencia = await crear_pago(client, method="card")
    receipt_id = await subir(client, referencia)

    await analizar(session, merchant, receipt_id, extraccion(), config=ajustes(reconciliation_auto_settle=True)
    )

    actualizado = await pago(session, referencia)
    await session.refresh(actualizado)
    assert actualizado.status is PaymentStatus.UNDER_REVIEW


# --- Señales de fraude derivadas del comprobante ----------------------------

async def test_indicios_de_manipulacion_generan_senal_de_fraude(client, session, merchant):
    referencia = await crear_pago(client)
    receipt_id = await subir(client, referencia)

    await analizar(session, merchant, receipt_id, extraccion(tampering_indicators=["tipografía inconsistente"])
    )

    actualizado = await pago(session, referencia)
    await session.refresh(actualizado)
    assert actualizado.status is PaymentStatus.UNDER_REVIEW

    codigos = {s["code"] for a in actualizado.assessments for s in a.signals}
    assert "RECEIPT_TAMPERING" in codigos


async def test_un_justificante_reutilizado_marca_el_pago(client, session, merchant):
    primera = await crear_pago(client, customer_id="cli_a")
    segunda = await crear_pago(client, customer_id="cli_b")

    await analizar(session, merchant, await subir(client, primera), extraccion())
    receipt = await analizar(session, merchant, await subir(client, segunda, PNG_1PX + b"\x00"), extraccion()
    )
    assert receipt.reconciliation_status is ReconciliationStatus.DUPLICATE

    actualizado = await pago(session, segunda)
    await session.refresh(actualizado)
    codigos = {s["code"] for a in actualizado.assessments for s in a.signals}
    assert "RECEIPT_DUPLICATE" in codigos


async def test_el_comprobante_nunca_rechaza_el_pago_por_si_solo(client, session, merchant):
    """Aun con la puntuación al máximo, la última palabra es de una persona."""
    primera = await crear_pago(client, customer_id="cli_a")
    segunda = await crear_pago(client, customer_id="cli_b")

    sospechoso = extraccion(tampering_indicators=["bordes retocados"])
    await analizar(session, merchant, await subir(client, primera), sospechoso)
    await analizar(session, merchant, await subir(client, segunda, PNG_1PX + b"\x00"), sospechoso)

    actualizado = await pago(session, segunda)
    await session.refresh(actualizado)

    puntuaciones = [a.score for a in actualizado.assessments]
    assert max(puntuaciones) == 100  # la evidencia es máxima...
    assert actualizado.status is PaymentStatus.UNDER_REVIEW  # ...pero no se rechaza solo


# --- Guardas de estado -------------------------------------------------------

async def test_un_comprobante_ilegible_no_toca_el_pago(client, session, merchant):
    referencia = await crear_pago(client)
    receipt_id = await subir(client, referencia)
    antes = (await pago(session, referencia)).status

    await analizar(session, merchant, receipt_id, extraccion(is_legible=False))

    actualizado = await pago(session, referencia)
    await session.refresh(actualizado)
    assert actualizado.status is antes


@pytest.mark.parametrize("estado_final", [PaymentStatus.SUCCEEDED, PaymentStatus.REFUNDED])
async def test_un_comprobante_tardio_no_deshace_un_pago_cerrado(client, session, estado_final, merchant):
    """Llegar tarde con un justificante no puede revertir lo ya decidido."""
    referencia = await crear_pago(client)
    receipt_id = await subir(client, referencia)

    cerrado = await pago(session, referencia)
    cerrado.status = estado_final
    cerrado.settled_at = datetime.now(UTC)
    await session.commit()

    await analizar(session, merchant, receipt_id, extraccion(tampering_indicators=["retocado"]))

    actualizado = await pago(session, referencia)
    await session.refresh(actualizado)
    assert actualizado.status is estado_final


# --- Resolución manual -------------------------------------------------------

async def test_aprobar_una_transferencia_no_llama_a_la_pasarela(client, session, merchant):
    """El dinero ya llegó: crear un cobro sería cobrar dos veces."""
    referencia = await crear_pago(client)
    receipt_id = await subir(client, referencia)
    await analizar(session, merchant, receipt_id, extraccion())

    response = await client.post(
        f"/api/v1/payments/{referencia}/review", json={"approve": True}, headers=AUTH_HEADERS
    )
    assert response.status_code == 200

    cuerpo = response.json()
    assert cuerpo["status"] == PaymentStatus.SUCCEEDED
    assert cuerpo["provider_payment_id"] is None  # nunca se tocó la pasarela
