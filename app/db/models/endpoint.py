"""Destinos de webhook de cada comercio y sus entregas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WebhookEndpoint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Dónde avisar a un comercio cuando algo le ocurre."""

    __tablename__ = "webhook_endpoints"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(512))
    # Secreto con el que se firma cada envío. El comercio lo necesita entero
    # para verificar, así que —a diferencia de las claves de API— se guarda
    # recuperable: no es una credencial de acceso a esta API.
    secret: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    event_types: Mapped[list[str]] = mapped_column(JSON, default=list)

    deliveries: Mapped[list[WebhookDelivery]] = relationship(
        back_populates="endpoint", cascade="all, delete-orphan"
    )

    def acepta(self, event_type: str) -> bool:
        """Sin filtro declarado, recibe todo."""
        return not self.event_types or event_type in self.event_types


class WebhookDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Un intento de entrega, guardado para poder auditar y reintentar."""

    __tablename__ = "webhook_deliveries"

    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), index=True
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    status_code: Mapped[int | None] = mapped_column(Integer, default=None)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(String(512), default=None)

    endpoint: Mapped[WebhookEndpoint] = relationship(back_populates="deliveries")

    __table_args__ = (Index("ix_deliveries_merchant_created", "merchant_id", "created_at"),)


class EventType:
    """Eventos que la plataforma emite hacia fuera."""

    PAYMENT_UPDATED = "payment.updated"
    RECEIPT_RECONCILED = "receipt.reconciled"
