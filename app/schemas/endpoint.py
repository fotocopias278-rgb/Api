"""Esquemas de destinos de webhook."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


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


class WebhookEndpointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    secret: str
    is_active: bool
    event_types: list[str]
    created_at: datetime
