"""Modelo de comprobante bancario."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.schemas.enums import ReceiptStatus, ReconciliationStatus

if TYPE_CHECKING:
    from app.db.models.payment import Payment


class Receipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "receipts"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True
    )
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), default=None, index=True
    )

    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    # Huella del fichero: detecta el mismo comprobante subido dos veces.
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_uri: Mapped[str | None] = mapped_column(String(512), default=None)
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    status: Mapped[ReceiptStatus] = mapped_column(
        Enum(ReceiptStatus, native_enum=False, length=32),
        default=ReceiptStatus.PENDING,
        index=True,
    )
    reconciliation_status: Mapped[ReconciliationStatus] = mapped_column(
        Enum(ReconciliationStatus, native_enum=False, length=32),
        default=ReconciliationStatus.PENDING,
        index=True,
    )

    # Se materializa fuera del JSON de extracción porque es la clave por la que
    # se busca un justificante reutilizado: dentro del JSON no hay índice que
    # valga y había que recorrer todos los comprobantes del comercio.
    operation_number: Mapped[str | None] = mapped_column(
        String(128), default=None, index=True
    )
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    confidence: Mapped[float | None] = mapped_column(Float, default=None)
    reconciliation_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(String(512), default=None)
    requeue_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    payment: Mapped[Payment | None] = relationship(back_populates="receipts", lazy="selectin")

    __table_args__ = (
        # La búsqueda de duplicados siempre es "este comercio, este número".
        Index("ix_receipts_merchant_operation", "merchant_id", "operation_number"),
        Index("ix_receipts_requeue", "status", "last_enqueued_at", "created_at"),
    )
