"""Webhook de Stripe: firma, idempotencia y cambios de estado."""

import hashlib
import hmac
import json
import time

from sqlalchemy import select

from app.db.models import Payment, WebhookEvent
from app.schemas.enums import PaymentStatus
from tests.conftest import AUTH_HEADERS

SECRETO = "whsec_test_secret"


def firmar(payload: bytes, secret: str = SECRETO, timestamp: int | None = None) -> str:
    """Reproduce el esquema de firma de Stripe: HMAC-SHA256 de `t.payload`."""
    ts = timestamp or int(time.time())
    firma = hmac.new(
        secret.encode(), f"{ts}.{payload.decode()}".encode(), hashlib.sha256
    ).hexdigest()
    return f"t={ts},v1={firma}"


def evento(tipo: str, provider_payment_id: str, event_id: str = "evt_1", **campos) -> bytes:
    objeto = {"id": provider_payment_id, **campos}
    if not tipo.startswith("payment_intent"):
        objeto = {"id": "ch_1", "payment_intent": provider_payment_id, **campos}
    return json.dumps(
        {"id": event_id, "type": tipo, "data": {"object": objeto}}
    ).encode()


async def enviar(client, payload: bytes, signature: str | None = None):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["Stripe-Signature"] = signature
    return await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)


async def crear_pago(client) -> tuple[str, str]:
    response = await client.post(
        "/api/v1/payments",
        json={"customer_id": "cliente-1", "amount_minor": 2_500, "currency": "EUR"},
        headers=AUTH_HEADERS,
    )
    body = response.json()
    return body["reference"], body["provider_payment_id"]


async def test_sin_firma_se_rechaza(client):
    response = await enviar(client, evento("payment_intent.succeeded", "pi_1"))
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_signature"


async def test_firma_invalida_se_rechaza(client):
    payload = evento("payment_intent.succeeded", "pi_1")
    response = await enviar(client, payload, firmar(payload, secret="whsec_otro"))
    assert response.status_code == 400


async def test_firma_caducada_se_rechaza(client):
    """Fuera de la tolerancia temporal: protege frente a reenvíos."""
    payload = evento("payment_intent.succeeded", "pi_1")
    antigua = int(time.time()) - 3_600
    response = await enviar(client, payload, firmar(payload, timestamp=antigua))
    assert response.status_code == 400


async def test_pago_liquidado_por_el_webhook(client, session):
    referencia, provider_id = await crear_pago(client)
    payload = evento("payment_intent.succeeded", provider_id)

    response = await enviar(client, payload, firmar(payload))
    assert response.status_code == 200
    assert response.json() == {"received": True, "applied": True}

    payment = await session.scalar(select(Payment).where(Payment.reference == referencia))
    assert payment.status is PaymentStatus.SUCCEEDED
    assert payment.settled_at is not None


async def test_pago_fallido_guarda_el_motivo(client, session):
    referencia, provider_id = await crear_pago(client)
    payload = evento(
        "payment_intent.payment_failed",
        provider_id,
        last_payment_error={"message": "Tarjeta rechazada por el emisor"},
    )

    await enviar(client, payload, firmar(payload))

    payment = await session.scalar(select(Payment).where(Payment.reference == referencia))
    assert payment.status is PaymentStatus.FAILED
    assert payment.failure_reason == "Tarjeta rechazada por el emisor"


async def test_devolucion_por_webhook_de_cargo(client, session):
    """En `charge.refunded` el intento viaja en `payment_intent`."""
    referencia, provider_id = await crear_pago(client)
    payload = evento("charge.refunded", provider_id)

    await enviar(client, payload, firmar(payload))

    payment = await session.scalar(select(Payment).where(Payment.reference == referencia))
    assert payment.status is PaymentStatus.REFUNDED


async def test_devolucion_parcial_por_webhook_guarda_el_acumulado(client, session):
    referencia, provider_id = await crear_pago(client)
    payload = evento(
        "charge.refunded",
        provider_id,
        event_id="evt_partial_refund",
        amount=2_500,
        amount_refunded=700,
    )
    await enviar(client, payload, firmar(payload))
    payment = await session.scalar(select(Payment).where(Payment.reference == referencia))
    assert payment.status is PaymentStatus.PARTIALLY_REFUNDED
    assert payment.refunded_amount_minor == 700


async def test_evento_antiguo_no_revierte_pago_liquidado(client, session):
    referencia, provider_id = await crear_pago(client)
    succeeded = evento("payment_intent.succeeded", provider_id, event_id="evt_success")
    failed = evento("payment_intent.payment_failed", provider_id, event_id="evt_old_failure")
    await enviar(client, succeeded, firmar(succeeded))
    await enviar(client, failed, firmar(failed))
    payment = await session.scalar(select(Payment).where(Payment.reference == referencia))
    assert payment.status is PaymentStatus.SUCCEEDED


async def test_el_evento_repetido_no_se_aplica_dos_veces(client, session):
    """Stripe reintenta hasta recibir 2xx: el segundo envío no debe rehacer nada."""
    _, provider_id = await crear_pago(client)
    payload = evento("payment_intent.succeeded", provider_id, event_id="evt_repetido")

    primera = await enviar(client, payload, firmar(payload))
    segunda = await enviar(client, payload, firmar(payload))

    assert primera.json() == {"received": True, "applied": True}
    assert segunda.json() == {"received": True, "duplicate": True}

    eventos = await session.execute(
        select(WebhookEvent).where(WebhookEvent.event_id == "evt_repetido")
    )
    assert len(list(eventos.scalars())) == 1


async def test_evento_de_tipo_no_gestionado_se_confirma(client):
    payload = evento("customer.created", "cus_1", event_id="evt_ignorado")
    response = await enviar(client, payload, firmar(payload))
    assert response.status_code == 200
    assert response.json()["applied"] is False


async def test_evento_de_un_pago_desconocido(client):
    payload = evento("payment_intent.succeeded", "pi_desconocido", event_id="evt_huerfano")
    response = await enviar(client, payload, firmar(payload))
    assert response.status_code == 200
    assert response.json()["applied"] is False


async def test_el_webhook_no_requiere_clave_de_api(client):
    """Se autentica con la firma HMAC, no con X-API-Key."""
    _, provider_id = await crear_pago(client)
    payload = evento("payment_intent.succeeded", provider_id, event_id="evt_sin_apikey")

    response = await enviar(client, payload, firmar(payload))
    assert response.status_code == 200
