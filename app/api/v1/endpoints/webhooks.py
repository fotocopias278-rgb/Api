"""Recepción de eventos de la pasarela de pago."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Request, status
from sqlalchemy import select

from app.api.deps import SessionDep, SettingsDep
from app.core.exceptions import NotFoundError
from app.db.models import Payment, WebhookEvent
from app.schemas.enums import PaymentStatus
from app.services.gateway import PaymentGatewayProtocol
from app.services.mercadopago_gateway import MercadoPagoGateway
from app.services.stripe_gateway import StripeGateway

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Proveedores admitidos en la ruta unificada.
PROVEEDORES = ("stripe", "mercadopago")


def gateway_de(provider: str, settings: SettingsDep) -> PaymentGatewayProtocol:
    """Devuelve el adaptador del proveedor indicado."""
    if provider == "mercadopago":
        return MercadoPagoGateway(settings)
    return StripeGateway(settings)


# Traducción de eventos de Mercado Pago a estados internos. Su vocabulario es
# distinto del de Stripe, de ahí la tabla aparte.
_ESTADO_MERCADOPAGO: dict[str, PaymentStatus] = {
    "approved": PaymentStatus.SUCCEEDED,
    "authorized": PaymentStatus.PROCESSING,
    "in_process": PaymentStatus.PROCESSING,
    "pending": PaymentStatus.PROCESSING,
    "rejected": PaymentStatus.FAILED,
    "cancelled": PaymentStatus.FAILED,
    "refunded": PaymentStatus.REFUNDED,
    "charged_back": PaymentStatus.REFUNDED,
}

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

    # Mismo criterio que la ruta unificada: `(provider, event_id)`. Dos
    # proveedores pueden emitir el mismo número y tratarlos como repetidos
    # haría perder avisos.
    existing = await session.scalar(
        select(WebhookEvent).where(
            WebhookEvent.provider == "stripe", WebhookEvent.event_id == event_id
        )
    )
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


@router.post(
    "/{provider}",
    status_code=status.HTTP_200_OK,
    summary="Webhook unificado por proveedor",
)
async def webhook_unificado(
    provider: Annotated[str, Path(description="stripe | mercadopago")],
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
    x_signature: Annotated[str | None, Header(alias="x-signature")] = None,
) -> dict[str, Any]:
    """Recibe avisos de cualquier pasarela soportada.

    La unicidad se comprueba por `(provider, event_id)` y no solo por el
    identificador: dos proveedores distintos pueden emitir el mismo número y
    tratarlos como repetidos haría perder avisos.
    """
    if provider not in PROVEEDORES:
        raise NotFoundError(f"Proveedor no soportado: {provider}.")

    payload = await request.body()
    gateway = gateway_de(provider, settings)
    firma = x_signature if provider == "mercadopago" else stripe_signature
    event = gateway.verify_webhook(payload, firma)

    event_id = str(event.get("id", ""))
    event_type = str(event.get("type", event.get("action", "")))

    existente = await session.scalar(
        select(WebhookEvent).where(
            WebhookEvent.provider == provider, WebhookEvent.event_id == event_id
        )
    )
    if existente is not None:
        logger.info(
            "webhook.duplicate_ignored", extra={"provider": provider, "event_id": event_id}
        )
        return {"received": True, "duplicate": True}

    registro = WebhookEvent(
        provider=provider, event_id=event_id, event_type=event_type, payload=dict(event)
    )
    session.add(registro)

    if provider == "mercadopago":
        aplicado = await _aplicar_mercadopago(session, event)
    else:
        aplicado = await _apply_event(session, event_type, event)

    registro.processed_at = datetime.now(UTC)
    await session.commit()

    logger.info(
        "webhook.processed",
        extra={"provider": provider, "event_id": event_id, "applied": aplicado},
    )
    return {"received": True, "applied": aplicado}


async def _aplicar_mercadopago(session: SessionDep, event: dict[str, Any]) -> bool:
    """Refleja un aviso de Mercado Pago en el pago correspondiente."""
    datos = event.get("data", {})
    provider_id = str(datos.get("id", "")) or str(event.get("data_id", ""))
    if not provider_id:
        return False

    nuevo = _ESTADO_MERCADOPAGO.get(str(datos.get("status", "")))
    if nuevo is None:
        return False

    payment = await session.scalar(
        select(Payment).where(Payment.provider_payment_id == provider_id)
    )
    if payment is None:
        logger.warning("webhook.payment_not_found", extra={"provider_payment_id": provider_id})
        return False

    payment.status = nuevo
    if nuevo is PaymentStatus.SUCCEEDED:
        payment.settled_at = datetime.now(UTC)
    if nuevo is PaymentStatus.FAILED:
        payment.failure_reason = str(datos.get("status_detail", ""))[:255] or "rechazado"
    return True
