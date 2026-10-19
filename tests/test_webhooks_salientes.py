"""Webhooks salientes: registro, firma, entrega y aislamiento."""

import hashlib
import hmac
import json

import httpx
import pytest

from app.db.models.endpoint import EventType, WebhookEndpoint
from app.services.notifications import (
    EVENT_HEADER,
    SIGNATURE_HEADER,
    NotificationService,
    firmar,
)
from tests.conftest import AUTH_HEADERS, AUTH_HEADERS_OTRO, MERCHANT_ID, MERCHANT_ID_OTRO

CUERPO = {"customer_id": "cli", "amount_minor": 2_500, "currency": "EUR"}


class Receptor:
    """Doble del servidor del comercio: guarda lo que le llega."""

    def __init__(self, status_code: int = 200, fallar: bool = False) -> None:
        self.status_code = status_code
        self.fallar = fallar
        self.recibidos: list[dict] = []

    def cliente(self) -> httpx.AsyncClient:
        async def manejar(request: httpx.Request) -> httpx.Response:
            if self.fallar:
                raise httpx.ConnectError("sin conexión")
            self.recibidos.append(
                {
                    "url": str(request.url),
                    "headers": dict(request.headers),
                    "body": request.content,
                }
            )
            return httpx.Response(self.status_code)

        return httpx.AsyncClient(transport=httpx.MockTransport(manejar))


async def registrar(session, merchant_id, url="https://comercio.example/hook", **kw):
    destino = WebhookEndpoint(
        merchant_id=merchant_id, url=url, secret="whsec_de_prueba_1234567890", **kw
    )
    session.add(destino)
    await session.commit()
    await session.refresh(destino)
    return destino


# --- Firma -------------------------------------------------------------------

def test_la_firma_sigue_el_esquema_de_stripe():
    """Formato conocido: cualquier integrador ya sabe verificarlo."""
    firma = firmar(b'{"a":1}', "secreto", timestamp=1700000000)
    assert firma.startswith("t=1700000000,v1=")

    esperado = hmac.new(
        b"secreto", b'1700000000.{"a":1}', hashlib.sha256
    ).hexdigest()
    assert firma.endswith(esperado)


def test_la_marca_de_tiempo_entra_en_la_firma():
    """Sin ella, un envío capturado se podría reproducir para siempre."""
    uno = firmar(b"{}", "s", timestamp=1)
    dos = firmar(b"{}", "s", timestamp=2)
    assert uno != dos


# --- Entrega -----------------------------------------------------------------

async def test_se_entrega_el_evento_firmado(session, settings):
    await registrar(session, MERCHANT_ID)
    receptor = Receptor()

    entregas = await NotificationService(
        session, config=settings, client=receptor.cliente()
    ).emit(MERCHANT_ID, EventType.PAYMENT_UPDATED, {"reference": "pay_1"})

    assert len(entregas) == 1
    assert entregas[0].delivered_at is not None
    assert entregas[0].status_code == 200

    enviado = receptor.recibidos[0]
    assert enviado["headers"][EVENT_HEADER.lower()] == EventType.PAYMENT_UPDATED
    assert enviado["headers"][SIGNATURE_HEADER.lower()].startswith("t=")

    cuerpo = json.loads(enviado["body"])
    assert cuerpo["type"] == EventType.PAYMENT_UPDATED
    assert cuerpo["data"]["reference"] == "pay_1"


async def test_el_comercio_puede_verificar_la_firma(session, settings):
    """Lo que importa de verdad: que la firma cuadre del otro lado."""
    destino = await registrar(session, MERCHANT_ID)
    receptor = Receptor()
    await NotificationService(session, config=settings, client=receptor.cliente()).emit(
        MERCHANT_ID, EventType.PAYMENT_UPDATED, {"reference": "pay_1"}
    )

    enviado = receptor.recibidos[0]
    firma = enviado["headers"][SIGNATURE_HEADER.lower()]
    ts = firma.split(",")[0].removeprefix("t=")
    recalculada = firmar(enviado["body"], destino.secret, timestamp=int(ts))
    assert hmac.compare_digest(firma, recalculada)


async def test_un_destino_caido_no_tumba_la_operacion(session, settings):
    """El pago ya ocurrió: no puede deshacerse porque su servidor no responda."""
    await registrar(session, MERCHANT_ID)
    receptor = Receptor(fallar=True)

    entregas = await NotificationService(
        session, config=settings, client=receptor.cliente()
    ).emit(MERCHANT_ID, EventType.PAYMENT_UPDATED, {"reference": "pay_1"})

    assert entregas[0].delivered_at is None
    assert "conexión" in entregas[0].last_error


async def test_un_error_del_destino_queda_registrado(session, settings):
    await registrar(session, MERCHANT_ID)
    receptor = Receptor(status_code=500)

    entregas = await NotificationService(
        session, config=settings, client=receptor.cliente()
    ).emit(MERCHANT_ID, EventType.PAYMENT_UPDATED, {})

    assert entregas[0].status_code == 500
    assert entregas[0].delivered_at is None
    assert entregas[0].last_error == "HTTP 500"


async def test_el_filtro_de_eventos_se_respeta(session, settings):
    await registrar(session, MERCHANT_ID, event_types=[EventType.RECEIPT_RECONCILED])
    receptor = Receptor()
    servicio = NotificationService(session, config=settings, client=receptor.cliente())

    assert await servicio.emit(MERCHANT_ID, EventType.PAYMENT_UPDATED, {}) == []
    assert len(await servicio.emit(MERCHANT_ID, EventType.RECEIPT_RECONCILED, {})) == 1


async def test_un_destino_inactivo_no_recibe(session, settings):
    await registrar(session, MERCHANT_ID, is_active=False)
    receptor = Receptor()
    entregas = await NotificationService(
        session, config=settings, client=receptor.cliente()
    ).emit(MERCHANT_ID, EventType.PAYMENT_UPDATED, {})
    assert entregas == []


async def test_los_eventos_no_se_cruzan_entre_comercios(session, settings):
    """El destino de un comercio jamás recibe lo que le pasa a otro."""
    await registrar(session, MERCHANT_ID, url="https://uno.example/hook")
    await registrar(session, MERCHANT_ID_OTRO, url="https://dos.example/hook")
    receptor = Receptor()

    await NotificationService(session, config=settings, client=receptor.cliente()).emit(
        MERCHANT_ID, EventType.PAYMENT_UPDATED, {}
    )
    assert [r["url"] for r in receptor.recibidos] == ["https://uno.example/hook"]


# --- Gestión por API ---------------------------------------------------------

async def test_registrar_un_destino_por_api(client):
    respuesta = await client.post(
        "/api/v1/webhook-endpoints",
        json={"url": "https://comercio.example/hook"},
        headers=AUTH_HEADERS,
    )
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    # El secreto sí es consultable: no da acceso, solo sirve para verificar.
    assert cuerpo["secret"].startswith("whsec_")


async def test_el_listado_de_destinos_esta_aislado(client):
    await client.post(
        "/api/v1/webhook-endpoints",
        json={"url": "https://uno.example/hook"},
        headers=AUTH_HEADERS,
    )
    ajeno = await client.get("/api/v1/webhook-endpoints", headers=AUTH_HEADERS_OTRO)
    assert ajeno.json() == []


async def test_no_se_borra_el_destino_de_otro(client):
    creado = await client.post(
        "/api/v1/webhook-endpoints",
        json={"url": "https://uno.example/hook"},
        headers=AUTH_HEADERS,
    )
    borrado = await client.delete(
        f"/api/v1/webhook-endpoints/{creado.json()['id']}", headers=AUTH_HEADERS_OTRO
    )
    assert borrado.status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "no-es-una-url",
        "ftp://servidor/x",
        "http://comercio.example/hook",
        "https://127.0.0.1/hook",
        "https://localhost/hook",
    ],
)
async def test_urls_invalidas(client, url):
    respuesta = await client.post(
        "/api/v1/webhook-endpoints", json={"url": url}, headers=AUTH_HEADERS
    )
    assert respuesta.status_code == 422


async def test_crear_un_pago_dispara_el_aviso(client, session):
    await registrar(session, MERCHANT_ID)
    await client.post("/api/v1/payments", json=CUERPO, headers=AUTH_HEADERS)

    from sqlalchemy import select

    from app.db.models.endpoint import WebhookDelivery

    entregas = await session.execute(
        select(WebhookDelivery).where(WebhookDelivery.merchant_id == MERCHANT_ID)
    )
    filas = list(entregas.scalars())
    tipos = {e.event_type for e in filas}
    assert EventType.PAYMENT_UPDATED in tipos
    assert all(e.attempts == 0 and e.delivered_at is None for e in filas)
