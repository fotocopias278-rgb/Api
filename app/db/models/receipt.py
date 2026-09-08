"""Modelo de comprobante bancario."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.schemas.enums import ReceiptStatus, ReconciliationStatus

if TYPE_CHECKING:
    from app.db.models.payment import Payment


class Receipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "receipts"

    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), default=None, index=True
    )

    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    # Huella del fichero: detecta el mismo comprobante subido dos veces.
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_uri: Mapped[str | None] = mapped_column(String(512), default=None)

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

    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    confidence: Mapped[float | None] = mapped_column(Float, default=None)
    reconciliation_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(String(512), default=None)

    payment: Mapped[Payment | None] = relationship(back_populates="receipts", lazy="selectin")
