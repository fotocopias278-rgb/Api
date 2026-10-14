"""Optimizaciones de PERF-01 y PERF-02."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import ApiKey, Receipt
from app.schemas.enums import ReceiptStatus
from app.services.merchants import MerchantService, _con_zona
from tests.conftest import API_KEY, AUTH_HEADERS, MERCHANT_ID
from tests.test_receipts_api import PNG_1PX, FakeVisionProvider, extraccion

# --- PERF-01: el número de operación se materializa e indexa ---------------

async def test_el_numero_de_operacion_se_guarda_en_su_columna(
    client, session, merchant, settings
):
    """Fuera del JSON: dentro no hay índice que valga."""
    from app.services.receipts import ReceiptService

    subida = await client.post(
        "/api/v1/receipts", files={"file": ("r.png", PNG_1PX, "image/png")},
        headers=AUTH_HEADERS,
    )
    receipt_id = uuid.UUID(subida.json()["id"])

    await ReceiptService(
        session, merchant=merchant, provider=FakeVisionProvider(extraccion()),
        config=settings,
    ).analyze(receipt_id)

    receipt = await session.get(Receipt, receipt_id)
    await session.refresh(receipt)
    assert receipt.operation_number == "OP-777"


async def test_el_duplicado_se_detecta_por_la_columna_indexada(
    client, session, merchant, settings
):
    """Mismo comportamiento que antes, con una consulta en vez de un bucle."""
    from app.services.receipts import ReceiptService

    async def crear_pago(cliente):
        r = await client.post(
            "/api/v1/payments",
            json={"customer_id": cliente, "amount_minor": 2_500, "currency": "EUR",
                  "method": "bank_transfer"},
            headers=AUTH_HEADERS,
        )
        return r.json()["reference"]

    primera, segunda = await crear_pago("a"), await crear_pago("b")

    async def subir(ref, contenido):
        r = await client.post(
            "/api/v1/receipts", files={"file": ("r.png", contenido, "image/png")},
            data={"payment_reference": ref}, headers=AUTH_HEADERS,
        )
        return uuid.UUID(r.json()["id"])

    servicio = ReceiptService(
        session, merchant=merchant, provider=FakeVisionProvider(extraccion()),
        config=settings,
    )
    uno = await servicio.analyze(await subir(primera, PNG_1PX))
    assert uno.reconciliation_status == "matched"

    dos = await servicio.analyze(await subir(segunda, PNG_1PX + b"\x00"))
    assert dos.reconciliation_status == "duplicate"


async def test_el_duplicado_no_cruza_comercios(client, session, merchant_otro, settings):
    """El índice es compuesto con merchant_id, no global."""
    from app.services.receipts import ReceiptService
    from tests.conftest import AUTH_HEADERS_OTRO

    # Un comprobante ya extraído del comercio uno, con número conocido.
    session.add(
        Receipt(
            merchant_id=MERCHANT_ID, filename="otro.png", content_type="image/png",
            size_bytes=10, content_sha256="x" * 64, status=ReceiptStatus.EXTRACTED,
            operation_number="OP-777", payment_id=None,
        )
    )
    await session.commit()

    pago = await client.post(
        "/api/v1/payments",
        json={"customer_id": "c", "amount_minor": 2_500, "currency": "EUR",
              "method": "bank_transfer"},
        headers=AUTH_HEADERS_OTRO,
    )
    subida = await client.post(
        "/api/v1/receipts", files={"file": ("r.png", PNG_1PX, "image/png")},
        data={"payment_reference": pago.json()["reference"]}, headers=AUTH_HEADERS_OTRO,
    )
    resultado = await ReceiptService(
        session, merchant=merchant_otro, provider=FakeVisionProvider(extraccion()),
        config=settings,
    ).analyze(uuid.UUID(subida.json()["id"]))

    assert resultado.reconciliation_status != "duplicate"


# --- PERF-02: no escribir en cada lectura autenticada ----------------------

async def test_la_primera_resolucion_sella_el_uso(session):
    clave = await session.scalar(select(ApiKey).where(ApiKey.merchant_id == MERCHANT_ID))
    clave.last_used_at = None
    await session.commit()

    await MerchantService(session).resolve_key(API_KEY)
    await session.refresh(clave)
    assert clave.last_used_at is not None


async def test_una_lectura_inmediata_no_vuelve_a_escribir(session):
    """Antes cada GET autenticado era además una escritura a disco."""
    servicio = MerchantService(session)
    await servicio.resolve_key(API_KEY)

    clave = await session.scalar(select(ApiKey).where(ApiKey.merchant_id == MERCHANT_ID))
    await session.refresh(clave)
    primera = clave.last_used_at

    await servicio.resolve_key(API_KEY)
    await session.refresh(clave)
    assert clave.last_used_at == primera


async def test_pasado_el_minuto_se_vuelve_a_sellar(session):
    servicio = MerchantService(session)
    await servicio.resolve_key(API_KEY)

    clave = await session.scalar(select(ApiKey).where(ApiKey.merchant_id == MERCHANT_ID))
    antiguo = datetime.now(UTC) - timedelta(minutes=5)
    clave.last_used_at = antiguo
    await session.commit()

    await servicio.resolve_key(API_KEY)
    await session.refresh(clave)
    # SQLite devuelve el valor sin zona: se normaliza igual que el código.
    assert _con_zona(clave.last_used_at) > antiguo


async def test_la_clave_sigue_resolviendo_al_comercio_correcto(session):
    merchant = await MerchantService(session).resolve_key(API_KEY)
    assert merchant is not None
    assert merchant.id == MERCHANT_ID
