"""Esquemas de destinos de webhook."""

from __future__ import annotations

import uuid
from datetime import datetime
from ipaddress import ip_address

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class WebhookEndpointCreate(BaseModel):
    url: HttpUrl
    secret: str | None = Field(
        default=None, min_length=16, max_length=128,
        description="Se genera uno si no lo aportas.",
    )
    event_types: list[str] = Field(
        default_factory=list,
        description="Vacío = todos los eventos.",
        examples=[["payment.updated", "receipt.reconciled"]],
    )

    @field_validator("url")
    @classmethod
    def _public_https_url(cls, url: HttpUrl) -> HttpUrl:
        if url.scheme != "https":
            raise ValueError("Los destinos de webhook deben usar HTTPS.")
        host = (url.host or "").lower().rstrip(".")
        if host.endswith((".localhost", ".local", ".internal", ".lan")) or host in {
            "localhost",
            "metadata.google.internal",
        }:
            raise ValueError("El destino de webhook no puede ser una dirección interna.")
        try:
            address = ip_address(host)
        except ValueError:
            return url
        if not address.is_global:
            raise ValueError("El destino de webhook debe ser una dirección pública.")
        return url


class WebhookEndpointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    secret: str
    is_active: bool
    event_types: list[str]
    created_at: datetime
