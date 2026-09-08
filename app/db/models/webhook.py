"""Registro de eventos entrantes de la pasarela (idempotencia)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WebhookEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Un evento por `event_id`: Stripe reintenta y no debe procesarse dos veces."""

    __tablename__ = "webhook_events"

    provider: Mapped[str] = mapped_column(String(32), default="stripe")
    event_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    error_message: Mapped[str | None] = mapped_column(String(512), default=None)
