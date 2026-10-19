"""Esquemas de entrada/salida para pagos."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field, field_validator

from app.core.money import format_amount
from app.schemas.enums import PaymentMethod, PaymentStatus, RiskDecision
from app.schemas.fraud import FraudAssessmentRead

CurrencyStr = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")]


class PaymentCreate(BaseModel):
    """Petición de creación de un pago."""

    model_config = ConfigDict(str_strip_whitespace=True)

    customer_id: str = Field(min_length=1, max_length=64)
    amount_minor: int = Field(gt=0, le=10**12, description="Importe en unidades mínimas.")
    currency: CurrencyStr
    method: PaymentMethod = PaymentMethod.CARD

    email: EmailStr | None = None
    ip_address: str | None = Field(default=None, max_length=45)
    card_bin: str | None = Field(default=None, min_length=6, max_length=8, pattern=r"^\d+$")
    card_country: str | None = Field(default=None, min_length=2, max_length=2)
    ip_country: str | None = Field(default=None, min_length=2, max_length=2)
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("card_country", "ip_country")
    @classmethod
    def _upper_country(cls, value: str | None) -> str | None:
        return value.upper() if value else None


class PaymentRead(BaseModel):
    """Representación pública de un pago."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(exclude=True)
    reference: str
    customer_id: str
    amount_minor: int
    refunded_amount_minor: int
    currency: str
    status: PaymentStatus
    method: PaymentMethod
    provider: str
    provider_payment_id: str | None
    risk_score: int | None
    risk_decision: RiskDecision | None
    failure_reason: str | None
    settled_at: datetime | None
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def amount(self) -> str:
        """Importe legible en la divisa del pago."""
        return format_amount(self.amount_minor, self.currency)


class PaymentDetail(PaymentRead):
    """Pago con su historial de evaluaciones de riesgo."""

    assessments: list[FraudAssessmentRead] = Field(default_factory=list)


class RefundRequest(BaseModel):
    amount_minor: int | None = Field(
        default=None, gt=0, description="Importe a devolver; por defecto, el total."
    )
    reason: str | None = Field(default=None, max_length=255)
