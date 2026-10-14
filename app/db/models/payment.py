"""Modelo de pago."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.schemas.enums import PaymentMethod, PaymentStatus, RiskDecision

if TYPE_CHECKING:
    from app.db.models.fraud import FraudAssessment
    from app.db.models.merchant import Merchant
    from app.db.models.receipt import Receipt


class Payment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "payments"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True
    )
    # Identificador público (`pay_...`): nunca se expone el UUID interno.
    reference: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True)

    # Importes en unidades mínimas (céntimos) para evitar aritmética flotante.
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))

    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, native_enum=False, length=32),
        default=PaymentStatus.PENDING,
        index=True,
    )
    method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, native_enum=False, length=32), default=PaymentMethod.CARD
    )

    provider: Mapped[str] = mapped_column(String(32), default="stripe")
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), unique=True, default=None)
    # Única por comercio, no global: dos comercios pueden usar "pedido-1".
    idempotency_key: Mapped[str | None] = mapped_column(String(128), default=None)

    # Señales de riesgo materializadas para poder filtrar sin join.
    risk_score: Mapped[int | None] = mapped_column(Integer, default=None)
    risk_decision: Mapped[RiskDecision | None] = mapped_column(
        Enum(RiskDecision, native_enum=False, length=32), default=None, index=True
    )

    email: Mapped[str | None] = mapped_column(String(320), default=None)
    ip_address: Mapped[str | None] = mapped_column(String(45), default=None)
    card_bin: Mapped[str | None] = mapped_column(String(8), default=None)
    card_country: Mapped[str | None] = mapped_column(String(2), default=None)
    ip_country: Mapped[str | None] = mapped_column(String(2), default=None)

    failure_reason: Mapped[str | None] = mapped_column(String(255), default=None)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    merchant: Mapped[Merchant] = relationship(back_populates="payments")
    assessments: Mapped[list[FraudAssessment]] = relationship(
        back_populates="payment", cascade="all, delete-orphan", lazy="selectin"
    )
    receipts: Mapped[list[Receipt]] = relationship(
        back_populates="payment", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        UniqueConstraint("merchant_id", "idempotency_key", name="uq_payments_merchant_idempotency"),
        Index("ix_payments_merchant_customer_created", "merchant_id", "customer_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuración
        return f"<Payment {self.reference} {self.amount_minor} {self.currency} {self.status}>"


def new_reference(prefix: str = "pay") -> str:
    """Referencia pública opaca y ordenable por creación."""
    return f"{prefix}_{uuid.uuid4().hex[:24]}"
