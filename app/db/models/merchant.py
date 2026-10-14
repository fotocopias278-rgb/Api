"""Comercios (inquilinos) y sus claves de API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.payment import Payment


class Merchant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Un cliente de la plataforma. Todo dato de negocio cuelga de uno."""

    __tablename__ = "merchants"

    name: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="merchant", cascade="all, delete-orphan", lazy="selectin"
    )
    payments: Mapped[list[Payment]] = relationship(back_populates="merchant")


class ApiKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Credencial de un comercio.

    Solo se guarda la huella SHA-256: el valor en claro se enseña una única
    vez al emitirla. Si se pierde, se revoca y se emite otra; no hay forma de
    recuperarla, y eso es deliberado.
    """

    __tablename__ = "api_keys"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    # Primeros caracteres en claro, para que el comercio identifique cuál es
    # cuál en su panel sin exponer la clave entera.
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    merchant: Mapped[Merchant] = relationship(back_populates="api_keys", lazy="selectin")

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
