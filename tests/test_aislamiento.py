"""Aislamiento entre comercios.

Es la propiedad que permite vender la API a más de un cliente: ningún comercio
puede ver, tocar ni deducir los datos de otro. Cada test mete datos en los dos
comercios y comprueba la separación desde fuera, por HTTP.
"""

import uuid

import pytest

from app.schemas.enums import PaymentStatus
from tests.conftest import AUTH_HEADERS, AUTH_HEADERS_OTRO
from tests.test_receipts_api import PNG_1PX

CUERPO = {"customer_id": "cli", "amount_minor": 2_500, "currency": "EUR"}


async def crear(client, headers, **extra):
    return await client.post("/api/v1/payments", json={**CUERPO, **extra}, headers=headers)


# --- Pagos -------------------------------------------------------------------

async def test_el_listado_solo_muestra_lo_propio(client):
    await crear(client, AUTH_HEADERS)
    await crear(client, AUTH_HEADERS)
    await crear(client, AUTH_HEADERS_OTRO)

    uno = await client.get("/api/v1/payments", headers=AUTH_HEADERS)
    dos = await client.get("/api/v1/payments", headers=AUTH_HEADERS_OTRO)

    assert uno.json()["total"] == 2
    assert dos.json()["total"] == 1


async def test_un_pago_ajeno_no_existe(client):
    """404, no 403: decir 'existe pero no es tuyo' ya filtra información."""
    referencia = (await crear(client, AUTH_HEADERS)).json()["reference"]

    propio = await client.get(f"/api/v1/payments/{referencia}", headers=AUTH_HEADERS)
    ajeno = await client.get(f"/api/v1/payments/{referencia}", headers=AUTH_HEADERS_OTRO)

    assert propio.status_code == 200
    assert ajeno.status_code == 404
    assert ajeno.json()["code"] == "not_found"


async def test_no_se_puede_devolver_un_pago_ajeno(client, session):
    from sqlalchemy import select

    from app.db.models import Payment

    referencia = (await crear(client, AUTH_HEADERS)).json()["reference"]
    pago = await session.scalar(select(Payment).where(Payment.reference == referencia))
    pago.status = PaymentStatus.SUCCEEDED
    await session.commit()

    ajeno = await client.post(f"/api/v1/payments/{referencia}/refund", headers=AUTH_HEADERS_OTRO)
    assert ajeno.status_code == 404

    propio = await client.post(f"/api/v1/payments/{referencia}/refund", headers=AUTH_HEADERS)
    assert propio.status_code == 200


async def test_no_se_puede_resolver_la_revision_de_un_pago_ajeno(client):
    for _ in range(6):
        await crear(client, AUTH_HEADERS, customer_id="veloz")
    referencia = (
        await crear(client, AUTH_HEADERS, customer_id="veloz", amount_minor=600_000)
    ).json()["reference"]

    ajeno = await client.post(
        f"/api/v1/payments/{referencia}/review", json={"approve": True}, headers=AUTH_HEADERS_OTRO
    )
    assert ajeno.status_code == 404


async def test_el_filtro_por_cliente_no_cruza_comercios(client):
    """Dos comercios pueden tener un cliente con el mismo identificador."""
    await crear(client, AUTH_HEADERS, customer_id="cliente-compartido")
    await crear(client, AUTH_HEADERS_OTRO, customer_id="cliente-compartido")

    uno = await client.get(
        "/api/v1/payments", params={"customer_id": "cliente-compartido"}, headers=AUTH_HEADERS
    )
    assert uno.json()["total"] == 1


# --- Idempotencia ------------------------------------------------------------

async def test_la_misma_clave_de_idempotencia_en_dos_comercios(client):
    """`pedido-1` es un identificador de negocio: cada comercio tiene el suyo."""
    cabeceras_uno = {**AUTH_HEADERS, "Idempotency-Key": "pedido-1"}
    cabeceras_dos = {**AUTH_HEADERS_OTRO, "Idempotency-Key": "pedido-1"}

    uno = await client.post("/api/v1/payments", json=CUERPO, headers=cabeceras_uno)
    dos = await client.post("/api/v1/payments", json=CUERPO, headers=cabeceras_dos)

    assert uno.status_code == dos.status_code == 201
    # Son pagos distintos, no una reutilización.
    assert uno.json()["reference"] != dos.json()["reference"]


# --- Riesgo ------------------------------------------------------------------

async def test_el_historial_de_riesgo_no_se_mezcla(client):
    """La velocidad de un comercio no puede marcar como sospechoso a otro."""
    for _ in range(8):
        await crear(client, AUTH_HEADERS, customer_id="comun")

    evaluacion = await client.post(
        "/api/v1/fraud/evaluate",
        json={"customer_id": "comun", "amount_minor": 2_500, "currency": "EUR"},
        headers=AUTH_HEADERS_OTRO,
    )
    codigos = {s["code"] for s in evaluacion.json()["signals"]}
    assert "VELOCITY" not in codigos


# --- Comprobantes ------------------------------------------------------------

async def test_un_comprobante_ajeno_no_existe(client):
    subida = await client.post(
        "/api/v1/receipts",
        files={"file": ("r.png", PNG_1PX, "image/png")},
        headers=AUTH_HEADERS,
    )
    receipt_id = subida.json()["id"]

    ajeno = await client.get(f"/api/v1/receipts/{receipt_id}", headers=AUTH_HEADERS_OTRO)
    assert ajeno.status_code == 404


async def test_el_mismo_fichero_en_dos_comercios_no_es_duplicado(client):
    """Avisar de duplicado entre comercios revelaría qué sube la competencia."""
    uno = await client.post(
        "/api/v1/receipts", files={"file": ("r.png", PNG_1PX, "image/png")}, headers=AUTH_HEADERS
    )
    dos = await client.post(
        "/api/v1/receipts",
        files={"file": ("r.png", PNG_1PX, "image/png")},
        headers=AUTH_HEADERS_OTRO,
    )

    assert uno.json()["duplicate_of"] is None
    assert dos.json()["duplicate_of"] is None


async def test_no_se_puede_asociar_un_comprobante_a_un_pago_ajeno(client):
    referencia = (await crear(client, AUTH_HEADERS)).json()["reference"]

    respuesta = await client.post(
        "/api/v1/receipts",
        files={"file": ("r.png", PNG_1PX, "image/png")},
        data={"payment_reference": referencia},
        headers=AUTH_HEADERS_OTRO,
    )
    assert respuesta.status_code == 404


# --- Credenciales ------------------------------------------------------------

async def test_una_clave_revocada_deja_de_servir(client, session):
    from app.services.merchants import MerchantService
    from tests.conftest import MERCHANT_ID

    assert (await client.get("/api/v1/payments", headers=AUTH_HEADERS)).status_code == 200

    merchant = await MerchantService(session).get_merchant(MERCHANT_ID)
    await MerchantService(session).revoke_key(MERCHANT_ID, merchant.api_keys[0].id)

    assert (await client.get("/api/v1/payments", headers=AUTH_HEADERS)).status_code == 401


async def test_un_comercio_desactivado_no_puede_operar(client, session):
    from app.db.models import Merchant
    from tests.conftest import MERCHANT_ID

    merchant = await session.get(Merchant, MERCHANT_ID)
    merchant.is_active = False
    await session.commit()

    assert (await client.get("/api/v1/payments", headers=AUTH_HEADERS)).status_code == 401


@pytest.mark.parametrize("clave", ["", "inventada", str(uuid.uuid4())])
async def test_claves_invalidas(client, clave):
    respuesta = await client.get("/api/v1/payments", headers={"X-API-Key": clave})
    assert respuesta.status_code == 401
