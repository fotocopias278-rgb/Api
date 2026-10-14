"""Emisión de webhooks hacia los comercios."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.db.models.endpoint import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-FinAI-Signature"
EVENT_HEADER = "X-FinAI-Event"


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
            entregas.append(await self._entregar(destino, event_type, payload))

        if entregas:
            await self._session.commit()
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

        cliente = self._client or httpx.AsyncClient(timeout=self._settings.webhook_timeout_seconds)
        propio = self._client is None
        try:
            respuesta = await cliente.post(
                destino.url,
                content=cuerpo,
                headers={
                    "Content-Type": "application/json",
                    EVENT_HEADER: event_type,
                    SIGNATURE_HEADER: firmar(cuerpo, destino.secret),
                },
            )
            entrega.status_code = respuesta.status_code
            if respuesta.is_success:
                from datetime import UTC, datetime

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
