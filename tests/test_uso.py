"""Medición de consumo facturable."""

from datetime import UTC, datetime, timedelta

from app.db.models import UsageKind
from app.services.usage import UsageService
from tests.conftest import ADMIN_HEADERS, AUTH_HEADERS, AUTH_HEADERS_OTRO, MERCHANT_ID
from tests.test_receipts_api import PNG_1PX

CUERPO = {"customer_id": "cli", "amount_minor": 2_500, "currency": "EUR"}


async def test_crear_un_pago_se_contabiliza(client, session):
    await client.post("/api/v1/payments", json=CUERPO, headers=AUTH_HEADERS)
    await client.post("/api/v1/payments", json=CUERPO, headers=AUTH_HEADERS)

    totales = await UsageService(session).summary(MERCHANT_ID)
    assert totales[UsageKind.PAYMENT_CREATED] == 2


async def test_subir_un_comprobante_se_contabiliza(client, session):
    await client.post(
        "/api/v1/receipts", files={"file": ("r.png", PNG_1PX, "image/png")}, headers=AUTH_HEADERS
    )
    totales = await UsageService(session).summary(MERCHANT_ID)
    assert totales[UsageKind.RECEIPT_UPLOADED] == 1


async def test_la_extraccion_se_contabiliza(client, session, merchant, settings):
    import uuid

    from app.services.receipts import ReceiptService
    from tests.test_receipts_api import FakeVisionProvider, extraccion

    subida = await client.post(
        "/api/v1/receipts", files={"file": ("r.png", PNG_1PX, "image/png")}, headers=AUTH_HEADERS
    )
    await ReceiptService(
        session, merchant=merchant, provider=FakeVisionProvider(extraccion()), config=settings
    ).analyze(uuid.UUID(subida.json()["id"]))

    totales = await UsageService(session).summary(MERCHANT_ID)
    assert totales[UsageKind.VISION_EXTRACTION] == 1


async def test_el_consumo_no_se_mezcla_entre_comercios(client, session):
    await client.post("/api/v1/payments", json=CUERPO, headers=AUTH_HEADERS)
    await client.post("/api/v1/payments", json=CUERPO, headers=AUTH_HEADERS)
    await client.post("/api/v1/payments", json=CUERPO, headers=AUTH_HEADERS_OTRO)

    propio = await client.get("/api/v1/usage", headers=AUTH_HEADERS)
    ajeno = await client.get("/api/v1/usage", headers=AUTH_HEADERS_OTRO)

    assert propio.json()["totals"][UsageKind.PAYMENT_CREATED] == 2
    assert ajeno.json()["totals"][UsageKind.PAYMENT_CREATED] == 1


async def test_un_pago_rechazado_tambien_cuenta(client, session):
    """Evaluar el riesgo consume recursos aunque la operación se bloquee."""
    await client.post(
        "/api/v1/payments",
        json={**CUERPO, "card_bin": "666666", "amount_minor": 900_000},
        headers=AUTH_HEADERS,
    )
    totales = await UsageService(session).summary(MERCHANT_ID)
    assert totales[UsageKind.PAYMENT_CREATED] == 1


async def test_el_periodo_acota_el_total(client, session):
    await client.post("/api/v1/payments", json=CUERPO, headers=AUTH_HEADERS)

    futuro = datetime.now(UTC) + timedelta(days=1)
    vacio = await UsageService(session).summary(MERCHANT_ID, desde=futuro)
    assert vacio[UsageKind.PAYMENT_CREATED] == 0

    pasado = datetime.now(UTC) - timedelta(days=1)
    lleno = await UsageService(session).summary(MERCHANT_ID, desde=pasado)
    assert lleno[UsageKind.PAYMENT_CREATED] == 1


async def test_los_tipos_sin_uso_aparecen_a_cero(client, session):
    """Una factura no debe tener huecos: lo no consumido vale cero, no falta."""
    totales = await UsageService(session).summary(MERCHANT_ID)
    assert set(totales) == {
        UsageKind.PAYMENT_CREATED,
        UsageKind.RECEIPT_UPLOADED,
        UsageKind.VISION_EXTRACTION,
    }
    assert all(v == 0 for v in totales.values())


async def test_el_administrador_consulta_el_consumo_de_cualquiera(client):
    await client.post("/api/v1/payments", json=CUERPO, headers=AUTH_HEADERS)
    respuesta = await client.get(
        f"/api/v1/admin/merchants/{MERCHANT_ID}/usage", headers=ADMIN_HEADERS
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["totals"][UsageKind.PAYMENT_CREATED] == 1


async def test_un_comercio_no_consulta_el_consumo_de_otro(client):
    from tests.conftest import MERCHANT_ID_OTRO

    respuesta = await client.get(
        f"/api/v1/admin/merchants/{MERCHANT_ID_OTRO}/usage", headers=AUTH_HEADERS
    )
    assert respuesta.status_code == 401
