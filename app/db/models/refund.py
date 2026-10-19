"""Operaciones de devolución con idempotencia persistente."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RefundOperation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refund_operations"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    requested_amount_minor: Mapped[int | None] = mapped_column(Integer, default=None)
    amount_minor: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(255), default=None)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    provider_refund_id: Mapped[str | None] = mapped_column(String(128), default=None)

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name="status_valid"),
        UniqueConstraint(
            "merchant_id", "idempotency_key", name="uq_refund_merchant_idempotency"
        ),
        Index("ix_refunds_payment_created", "payment_id", "created_at"),
    )
