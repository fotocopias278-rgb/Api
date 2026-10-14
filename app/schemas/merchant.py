"""Esquemas de comercios y claves de API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MerchantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    meta: dict[str, Any] = Field(default_factory=dict)


class MerchantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    is_active: bool
    meta: dict[str, Any]
    created_at: datetime


class ApiKeyRead(BaseModel):
    """Una clave ya emitida. Nunca incluye el secreto."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyIssued(ApiKeyRead):
    """Respuesta al emitir: la única vez que el secreto viaja."""

    secret: str = Field(
        description="Guárdala ahora: no se puede recuperar, solo revocar y emitir otra."
    )


class ApiKeyCreate(BaseModel):
    name: str = Field(default="default", min_length=1, max_length=120)
