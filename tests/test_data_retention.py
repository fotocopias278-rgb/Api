"""Borrado de datos personales vencidos según el periodo de retención."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import Payment, WebhookEvent
from app.db.models.endpoint import WebhookDelivery, WebhookEndpoint
from app.schemas.enums import PaymentMethod, PaymentStatus
from app.workers.tasks import _anonymize_expired_payment_data_in_session
from tests.conftest import MERCHANT_ID


async def test_anonimiza_email_e_ip_de_pagos_vencidos_sin_tocar_registros_recientes(session):
    now = datetime.now(UTC)
    expired = Payment(
        merchant_id=MERCHANT_ID,
        reference="pay_retention_expired",
        customer_id="retention-customer",
        amount_minor=2_000,
        currency="EUR",
        method=PaymentMethod.CARD,
        status=PaymentStatus.SUCCEEDED,
        email="persona@example.com",
        ip_address="203.0.113.9",
        created_at=now - timedelta(days=181),
    )
    recent = Payment(
        merchant_id=MERCHANT_ID,
        reference="pay_retention_recent",
        customer_id="retention-customer",
        amount_minor=2_000,
        currency="EUR",
        method=PaymentMethod.CARD,
        status=PaymentStatus.SUCCEEDED,
        email="persona@example.com",
        ip_address="203.0.113.10",
        created_at=now - timedelta(days=179),
    )
    endpoint = WebhookEndpoint(
        merchant_id=MERCHANT_ID,
        url="https://merchant.example/hook",
        secret="whsec_retention_test",
    )
    session.add_all([expired, recent, endpoint])
    await session.flush()
    retained_delivery = WebhookDelivery(
        endpoint_id=endpoint.id,
        merchant_id=MERCHANT_ID,
        event_type="payment.updated",
        payload={"customer": {"email": "persona@example.com", "ip_address": "203.0.113.9"}},
        created_at=now - timedelta(days=181),
    )
    expired_delivery = WebhookDelivery(
        endpoint_id=endpoint.id,
        merchant_id=MERCHANT_ID,
        event_type="payment.updated",
        payload={"email": "persona@example.com"},
        created_at=now - timedelta(days=181),
        delivered_at=now - timedelta(days=181),
    )
    event = WebhookEvent(
        merchant_id=MERCHANT_ID,
        provider="stripe",
        event_id="evt_retention_expired",
        event_type="payment_intent.succeeded",
        payload={"data": {"object": {"email": "persona@example.com", "ip": "203.0.113.9"}}},
        created_at=now - timedelta(days=181),
    )
    session.add_all([retained_delivery, expired_delivery, event])
    await session.commit()

    count = await _anonymize_expired_payment_data_in_session(
        session, 180, now=now
    )

    await session.refresh(expired)
    await session.refresh(recent)
    await session.refresh(retained_delivery)
    await session.refresh(event)
    assert count == 1
    assert expired.email is None
    assert expired.ip_address is None
    assert recent.email == "persona@example.com"
    assert recent.ip_address == "203.0.113.10"
    assert retained_delivery.payload["customer"] == {
        "email": None,
        "ip_address": None,
    }
    assert event.payload["data"]["object"] == {"email": None, "ip": None}
    assert await session.get(WebhookDelivery, expired_delivery.id) is None
    assert await session.scalar(
        select(Payment.id).where(Payment.reference == expired.reference)
    )
