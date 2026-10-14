"""Recepción de eventos de la pasarela de pago."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, status
from sqlalchemy import select

from app.api.deps import SessionDep, SettingsDep
from app.db.models import Payment, WebhookEvent
from app.schemas.enums import PaymentStatus
from app.services.stripe_gateway import StripeGateway

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Traducción de eventos de Stripe a estados internos.
_EVENT_STATUS: dict[str, PaymentStatus] = {
    "payment_intent.succeeded": PaymentStatus.SUCCEEDED,
    "payment_intent.payment_failed": PaymentStatus.FAILED,
    "payment_intent.canceled": PaymentStatus.FAILED,
    "charge.refunded": PaymentStatus.REFUNDED,
}


@router.post(
    "/stripe",
    status_code=status.HTTP_200_OK,
    summary="Webhook de Stripe",
    # El endpoint se autentica con la firma HMAC de Stripe, no con la clave de API.
    dependencies=[],
)
async def stripe_webhook(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict[str, Any]:
    """Procesa un evento de Stripe de forma idempotente.

    Stripe reintenta cada evento hasta recibir un 2xx, así que un `event_id` ya
    visto se confirma sin volver a aplicar el cambio de estado.
    """
    payload = await request.body()
    gateway = StripeGateway(settings)
    event = gateway.verify_webhook(payload, stripe_signature)

    event_id = str(event.get("id", ""))
    event_type = str(event.get("type", ""))

    existing = await session.scalar(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
    if existing is not None:
        logger.info("webhook.duplicate_ignored", extra={"event_id": event_id})
        return {"received": True, "duplicate": True}

    record = WebhookEvent(
        provider="stripe", event_id=event_id, event_type=event_type, payload=dict(event)
    )
    session.add(record)

    applied = await _apply_event(session, event_type, event)
    record.processed_at = datetime.now(UTC)
    await session.commit()

    logger.info(
        "webhook.processed",
        extra={"event_id": event_id, "event_type": event_type, "applied": applied},
    )
    return {"received": True, "applied": applied}


async def _apply_event(session: SessionDep, event_type: str, event: dict[str, Any]) -> bool:
    """Refleja el evento en el pago correspondiente. Devuelve si hubo cambio."""
    new_status = _EVENT_STATUS.get(event_type)
    if new_status is None:
        return False

    obj = event.get("data", {}).get("object", {})
    # En `charge.*` el identificador del intento viaja en `payment_intent`.
    provider_id = (
        obj.get("id")
        if event_type.startswith("payment_intent")
        else obj.get("payment_intent")
    )
    if not provider_id:
        return False

    # Deliberadamente sin filtro de comercio: Stripe notifica a la plataforma,
    # no a un inquilino, y la petición no trae clave de API con la que acotar.
    # Lo que la hace segura es la firma HMAC verificada antes de llegar aquí,
    # más que `provider_payment_id` es único en toda la tabla.
    payment = await session.scalar(
        select(Payment).where(Payment.provider_payment_id == provider_id)
    )
    if payment is None:
        logger.warning("webhook.payment_not_found", extra={"provider_payment_id": provider_id})
        return False

    payment.status = new_status
    if new_status is PaymentStatus.SUCCEEDED:
        payment.settled_at = datetime.now(UTC)
    if new_status is PaymentStatus.FAILED:
        failure = obj.get("last_payment_error") or {}
        payment.failure_reason = str(failure.get("message", event_type))[:255]
    return True
