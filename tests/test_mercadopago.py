"""Pasarela de Mercado Pago y ruta unificada de webhooks."""

import hashlib
import hmac
import json
import time

import httpx
import pytest

from app.core.exceptions import GatewayError, WebhookVerificationError
from app.services.gateway import PaymentGatewayProtocol
from app.services.mercadopago_gateway import MercadoPagoGateway
from app.services.stripe_gateway import StripeGateway
from tests.conftest import ajustes_con

SECRETO = "mp_secreto_de_prueba"


def ajustes_mp(**kw):
    return ajustes_con(mercadopago_webhook_secret=SECRETO, **kw)


def firmar_mp(payload: bytes, secreto: str = SECRETO, ts: int | None = None) -> str:
    marca = ts or int(time.time())
    mac = hmac.new(secreto.encode(), f"{marca}.{payload.decode()}".encode(), hashlib.sha256)
    return f"ts={marca},v1={mac.hexdigest()}"


# --- Contrato común ---------------------------------------------------------

@pytest.mark.parametrize("gateway", [StripeGateway(), MercadoPagoGateway()])
def test_ambas_pasarelas_cumplen_el_protocolo(gateway):
    """El dominio no debe saber con cuál habla."""
    assert isinstance(gateway, PaymentGatewayProtocol)


# --- Modo simulado ----------------------------------------------------------

async def test_sin_credenciales_opera_en_modo_simulado():
    gateway = MercadoPagoGateway(ajustes_con(mercadopago_access_token=None))
    assert not gateway.enabled

    cobro = await gateway.create_payment_intent(
        amount_minor=128_450, currency="EUR", reference="pay_x", customer_id="c1"
    )
    assert cobro.provider_payment_id.startswith("mp_sim_")
    assert not cobro.succeeded


async def test_la_devolucion_simulada_tambien(ajustes_mp=None):
    gateway = MercadoPagoGateway(ajustes_con(mercadopago_access_token=None))
    cobro = await gateway.refund(provider_payment_id="mp_sim_1")
    assert cobro.status == "refunded"


# --- Llamada real -----------------------------------------------------------

class ApiFalsa:
    """Doble de la API de Mercado Pago."""

    def __init__(self, status_code: int = 201, cuerpo: dict | None = None):
        self.status_code = status_code
        self.cuerpo = cuerpo or {"id": 112233, "status": "approved"}
        self.peticiones: list[httpx.Request] = []

    def cliente(self) -> httpx.AsyncClient:
        async def manejar(request: httpx.Request) -> httpx.Response:
            self.peticiones.append(request)
            return httpx.Response(self.status_code, json=self.cuerpo)

        return httpx.AsyncClient(
            transport=httpx.MockTransport(manejar), base_url="https://api.mercadopago.com"
        )


async def test_el_cobro_viaja_con_clave_de_idempotencia():
    """Sin ella, un reintento de red cobraría dos veces."""
    api = ApiFalsa()
    gateway = MercadoPagoGateway(
        ajustes_con(mercadopago_access_token="TEST-token"), client=api.cliente()
    )
    cobro = await gateway.create_payment_intent(
        amount_minor=128_450, currency="EUR", reference="pay_1",
        customer_id="c1", idempotency_key="pedido-7",
    )

    peticion = api.peticiones[0]
    assert peticion.headers["X-Idempotency-Key"] == "pedido-7"
    assert str(peticion.url).endswith("/v1/payments")
    assert cobro.provider_payment_id == "112233"
    assert cobro.succeeded  # "approved" cuenta como éxito


async def test_el_importe_viaja_en_decimal_no_en_centimos():
    """Mercado Pago no usa unidades mínimas, a diferencia de Stripe."""
    api = ApiFalsa()
    gateway = MercadoPagoGateway(
        ajustes_con(mercadopago_access_token="TEST-token"), client=api.cliente()
    )
    await gateway.create_payment_intent(
        amount_minor=128_450, currency="EUR", reference="pay_1", customer_id="c1"
    )
    enviado = json.loads(api.peticiones[0].content)
    assert enviado["transaction_amount"] == 1284.50


async def test_las_divisas_sin_decimales_no_se_dividen():
    """El peso chileno no tiene céntimos: dividir entre 100 lo rompería."""
    api = ApiFalsa()
    gateway = MercadoPagoGateway(
        ajustes_con(mercadopago_access_token="TEST-token"), client=api.cliente()
    )
    await gateway.create_payment_intent(
        amount_minor=50_000, currency="CLP", reference="pay_1", customer_id="c1"
    )
    assert json.loads(api.peticiones[0].content)["transaction_amount"] == 50_000


async def test_un_error_del_proveedor_se_traduce_al_dominio():
    api = ApiFalsa(status_code=400, cuerpo={"message": "invalid"})
    gateway = MercadoPagoGateway(
        ajustes_con(mercadopago_access_token="TEST-token"), client=api.cliente()
    )
    with pytest.raises(GatewayError, match="rechazó el cobro"):
        await gateway.create_payment_intent(
            amount_minor=100, currency="EUR", reference="pay_1", customer_id="c1"
        )


# --- Firma del webhook ------------------------------------------------------

def test_una_firma_valida_se_acepta():
    cuerpo = json.dumps({"id": 1, "action": "payment.updated"}).encode()
    evento = MercadoPagoGateway(ajustes_mp()).verify_webhook(cuerpo, firmar_mp(cuerpo))
    assert evento["action"] == "payment.updated"


@pytest.mark.parametrize(
    ("firma", "motivo"),
    [
        (None, "ausente"),
        ("basura", "malformada"),
        ("ts=1,v1=deadbeef", "hmac incorrecto"),
    ],
)
def test_las_firmas_invalidas_se_rechazan(firma, motivo):
    cuerpo = b'{"id":1}'
    with pytest.raises(WebhookVerificationError):
        MercadoPagoGateway(ajustes_mp()).verify_webhook(cuerpo, firma)


def test_una_firma_caducada_se_rechaza():
    """Protege frente al reenvío de un aviso capturado."""
    cuerpo = b'{"id":1}'
    antigua = int(time.time()) - 3_600
    with pytest.raises(WebhookVerificationError, match="caducado"):
        MercadoPagoGateway(ajustes_mp()).verify_webhook(cuerpo, firmar_mp(cuerpo, ts=antigua))


def test_sin_secreto_configurado_no_se_acepta_nada():
    cuerpo = b'{"id":1}'
    with pytest.raises(GatewayError, match="MERCADOPAGO_WEBHOOK_SECRET"):
        MercadoPagoGateway(ajustes_con(mercadopago_webhook_secret=None)).verify_webhook(
            cuerpo, "ts=1,v1=x"
        )


# --- Ruta unificada de webhooks ---------------------------------------------

async def test_la_ruta_unificada_acepta_mercadopago(client, session, monkeypatch):
    """`POST /webhooks/mercadopago` aplica el aviso al pago correspondiente."""
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.db.models import Payment
    from app.schemas.enums import PaymentStatus
    from tests.conftest import AUTH_HEADERS

    monkeypatch.setattr(get_settings(), "mercadopago_webhook_secret", SECRETO)

    pago = await client.post(
        "/api/v1/payments",
        json={"customer_id": "c", "amount_minor": 2_500, "currency": "EUR"},
        headers=AUTH_HEADERS,
    )
    referencia = pago.json()["reference"]
    registro = await session.scalar(select(Payment).where(Payment.reference == referencia))
    registro.provider_payment_id = "mp-998877"
    await session.commit()

    cuerpo = json.dumps(
        {"id": 777, "action": "payment.updated", "data": {"id": "mp-998877", "status": "approved"}}
    ).encode()
    respuesta = await client.post(
        "/api/v1/webhooks/mercadopago",
        content=cuerpo,
        headers={"Content-Type": "application/json", "x-signature": firmar_mp(cuerpo)},
    )

    assert respuesta.status_code == 200
    assert respuesta.json() == {"received": True, "applied": True}

    await session.refresh(registro)
    assert registro.status is PaymentStatus.SUCCEEDED
    assert registro.settled_at is not None


async def test_la_ruta_unificada_rechaza_firmas_invalidas(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "mercadopago_webhook_secret", SECRETO)
    respuesta = await client.post(
        "/api/v1/webhooks/mercadopago",
        content=b'{"id":1}',
        headers={"Content-Type": "application/json", "x-signature": "ts=1,v1=falsa"},
    )
    assert respuesta.status_code == 400
    assert respuesta.json()["code"] == "invalid_signature"


async def test_un_proveedor_desconocido_es_404(client):
    respuesta = await client.post("/api/v1/webhooks/paypal", content=b"{}")
    assert respuesta.status_code == 404


async def test_el_evento_repetido_no_se_aplica_dos_veces(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "mercadopago_webhook_secret", SECRETO)
    cuerpo = json.dumps(
        {"id": 555, "action": "payment.updated", "data": {"id": "x", "status": "approved"}}
    ).encode()
    cabeceras = {"Content-Type": "application/json", "x-signature": firmar_mp(cuerpo)}

    primera = await client.post("/api/v1/webhooks/mercadopago", content=cuerpo, headers=cabeceras)
    segunda = await client.post("/api/v1/webhooks/mercadopago", content=cuerpo, headers=cabeceras)

    assert primera.json()["received"] is True
    assert segunda.json() == {"received": True, "duplicate": True}


async def test_el_mismo_identificador_en_dos_proveedores_no_es_duplicado(
    client, session, monkeypatch
):
    """La unicidad es por (provider, event_id): si no, se perderían avisos."""
    from app.core.config import get_settings
    from app.db.models import WebhookEvent

    monkeypatch.setattr(get_settings(), "mercadopago_webhook_secret", SECRETO)

    # Un evento de Stripe con identificador "evt_colision" ya registrado.
    session.add(
        WebhookEvent(provider="stripe", event_id="evt_colision", event_type="x", payload={})
    )
    await session.commit()

    cuerpo = json.dumps(
        {"id": "evt_colision", "action": "payment.updated", "data": {"id": "y", "status": "pending"}}
    ).encode()
    respuesta = await client.post(
        "/api/v1/webhooks/mercadopago",
        content=cuerpo,
        headers={"Content-Type": "application/json", "x-signature": firmar_mp(cuerpo)},
    )
    assert "duplicate" not in respuesta.json()


async def test_la_ruta_antigua_de_stripe_sigue_funcionando(client):
    """Compatibilidad: los clientes actuales apuntan a /webhooks/stripe."""
    respuesta = await client.post("/api/v1/webhooks/stripe", content=b"{}")
    # Sin firma válida devuelve 400, no 404: la ruta existe y la valida.
    assert respuesta.status_code == 400
