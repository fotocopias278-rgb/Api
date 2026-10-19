"""Emisión de webhooks hacia los comercios."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import socket
import time
import uuid
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.db.models.endpoint import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-FinAI-Signature"
EVENT_HEADER = "X-FinAI-Event"
DELIVERY_HEADER = "X-FinAI-Delivery"


class WebhookDeliveryFailure(Exception):
    """Fallo transitorio de entrega que permite reintentos de Celery."""


async def _validar_destino_publico(url: str, timeout: float) -> None:
    host = urlsplit(url).hostname
    if not host:
        raise ValueError("URL sin host")
    try:
        addresses = [ip_address(host)]
    except ValueError:
        records = await asyncio.wait_for(
            asyncio.to_thread(
                socket.getaddrinfo,
                host,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            ),
            timeout=timeout,
        )
        addresses = [ip_address(record[4][0]) for record in records]
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("El host resuelve a una IP no pública")


def enqueue_webhook_delivery(delivery_id: uuid.UUID) -> None:
    from app.workers.tasks import deliver_webhook_task

    deliver_webhook_task.delay(str(delivery_id))


def firmar(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Firma con el mismo esquema que usa Stripe: `t=<ts>,v1=<hmac>`.

    Se incluye la marca de tiempo dentro de lo firmado para que un envío
    capturado no pueda reproducirse indefinidamente, y se reutiliza el formato
    de Stripe porque cualquier integrador ya sabe verificarlo.
    """
    ts = timestamp or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.{payload.decode()}".encode(), hashlib.sha256)
    return f"t={ts},v1={mac.hexdigest()}"


class NotificationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        config: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._session = session
        self._settings = config or default_settings
        self._client = client

    async def emit(
        self, merchant_id: uuid.UUID, event_type: str, payload: dict[str, Any]
    ) -> list[WebhookDelivery]:
        """Envía el evento a los destinos activos del comercio.

        Un fallo de entrega no se propaga: el comercio se enteró o no, pero la
        operación que lo provocó ya ocurrió y no puede deshacerse porque su
        servidor esté caído. El intento queda registrado para reintentarlo.
        """
        destinos = await self._session.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.merchant_id == merchant_id,
                WebhookEndpoint.is_active.is_(True),
            )
        )
        entregas: list[WebhookDelivery] = []

        for destino in destinos.scalars():
            if not destino.acepta(event_type):
                continue
            if self._client is None and self._settings.webhook_delivery_mode == "celery":
                entrega = WebhookDelivery(
                    endpoint_id=destino.id,
                    merchant_id=destino.merchant_id,
                    event_type=event_type,
                    payload=payload,
                    attempts=0,
                    last_enqueued_at=datetime.now(UTC),
                )
                self._session.add(entrega)
                entregas.append(entrega)
            else:
                entregas.append(await self._entregar(destino, event_type, payload))

        if entregas:
            await self._session.commit()
        if self._client is None and self._settings.webhook_delivery_mode == "celery":
            for entrega in entregas:
                try:
                    enqueue_webhook_delivery(entrega.id)
                except Exception as exc:
                    logger.error(
                        "webhook.enqueue_failed",
                        extra={"delivery_id": str(entrega.id), "error": str(exc)},
                        exc_info=True,
                    )
        return entregas

    async def _entregar(
        self, destino: WebhookEndpoint, event_type: str, payload: dict[str, Any]
    ) -> WebhookDelivery:
        cuerpo = json.dumps(
            {"type": event_type, "created": int(time.time()), "data": payload},
            default=str,
            ensure_ascii=False,
        ).encode()

        entrega = WebhookDelivery(
            endpoint_id=destino.id,
            merchant_id=destino.merchant_id,
            event_type=event_type,
            payload=payload,
            attempts=1,
        )
        self._session.add(entrega)
        await self._session.flush()

        if self._client is None:
            try:
                await _validar_destino_publico(
                    destino.url, self._settings.webhook_timeout_seconds
                )
            except (OSError, TimeoutError, ValueError) as exc:
                entrega.last_error = f"Destino de webhook no permitido o no resoluble: {exc}"[:512]
                logger.warning(
                    "webhook.destino_rechazado",
                    extra={"endpoint_id": str(destino.id), "error": str(exc)},
                )
                return entrega

        cliente = self._client or httpx.AsyncClient(
            timeout=self._settings.webhook_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        propio = self._client is None
        try:
            respuesta = await cliente.post(
                destino.url,
                content=cuerpo,
                headers={
                    "Content-Type": "application/json",
                    EVENT_HEADER: event_type,
                    DELIVERY_HEADER: str(entrega.id),
                    SIGNATURE_HEADER: firmar(cuerpo, destino.secret),
                },
            )
            entrega.status_code = respuesta.status_code
            if respuesta.is_success:
                entrega.delivered_at = datetime.now(UTC)
            else:
                entrega.last_error = f"HTTP {respuesta.status_code}"
        except httpx.HTTPError as exc:
            entrega.last_error = str(exc)[:512]
            logger.warning(
                "webhook.entrega_fallida",
                extra={"endpoint_id": str(destino.id), "error": str(exc)},
            )
        finally:
            if propio:
                await cliente.aclose()

        return entrega

async def deliver_persisted_webhook(delivery_id: uuid.UUID) -> None:
    """Envía una entrega persistida sin mantener una transacción de DB abierta."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        delivery = await session.get(WebhookDelivery, delivery_id)
        if delivery is None or delivery.delivered_at is not None:
            return
        endpoint = await session.get(WebhookEndpoint, delivery.endpoint_id)
        if endpoint is None:
            return
        if not endpoint.is_active:
            delivery.last_error = "Destino desactivado antes de la entrega."
            await session.commit()
            return
        url, secret = endpoint.url, endpoint.secret
        event_type, payload = delivery.event_type, delivery.payload
        delivery.attempts += 1
        await session.commit()

    cuerpo = json.dumps(
        {"type": event_type, "created": int(time.time()), "data": payload},
        default=str,
        ensure_ascii=False,
    ).encode()
    status_code: int | None = None
    error: str | None = None
    delivered_at: datetime | None = None
    try:
        await _validar_destino_publico(url, default_settings.webhook_timeout_seconds)
        async with httpx.AsyncClient(
            timeout=default_settings.webhook_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(
                url,
                content=cuerpo,
                headers={
                    "Content-Type": "application/json",
                    EVENT_HEADER: event_type,
                    DELIVERY_HEADER: str(delivery_id),
                    SIGNATURE_HEADER: firmar(cuerpo, secret),
                },
            )
        status_code = response.status_code
        if response.is_success:
            delivered_at = datetime.now(UTC)
        else:
            error = f"HTTP {response.status_code}"
    except ValueError as exc:
        error = f"Destino de webhook rechazado: {exc}"[:512]
    except (httpx.HTTPError, OSError, TimeoutError) as exc:
        error = str(exc)[:512]

    async with SessionFactory() as session:
        delivery = await session.get(WebhookDelivery, delivery_id)
        if delivery is None:
            return
        delivery.status_code = status_code
        delivery.delivered_at = delivered_at
        delivery.last_error = error
        await session.commit()

    if error and not error.startswith("Destino de webhook rechazado:"):
        raise WebhookDeliveryFailure(error)
