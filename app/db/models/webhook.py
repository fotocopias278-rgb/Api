"""Registro de eventos entrantes de la pasarela (idempotencia)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WebhookEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Un evento por `event_id`: Stripe reintenta y no debe procesarse dos veces."""

    __tablename__ = "webhook_events"

    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(32), default="stripe")
    # Único por proveedor, no global: dos pasarelas pueden emitir el mismo
    # número de evento y tratarlos como repetidos haría perder avisos.
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    error_message: Mapped[str | None] = mapped_column(String(512), default=None)

    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_webhook_events_provider_event"),
        Index("ix_webhook_events_merchant_created", "merchant_id", "created_at"),
    )
