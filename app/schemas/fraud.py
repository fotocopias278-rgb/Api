"""Esquemas del motor de detección de fraude."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.enums import RiskDecision


class FraudSignalRead(BaseModel):
    code: str
    description: str
    weight: int


class FraudEvaluationRequest(BaseModel):
    """Evaluación sin persistencia: permite puntuar antes de cobrar."""

    customer_id: str = Field(min_length=1, max_length=64)
    amount_minor: int = Field(gt=0)
    currency: Annotated[str, Field(min_length=3, max_length=3)]
    email: EmailStr | None = None
    card_bin: str | None = Field(default=None, min_length=6, max_length=8, pattern=r"^\d+$")
    card_country: str | None = Field(default=None, min_length=2, max_length=2)
    ip_country: str | None = Field(default=None, min_length=2, max_length=2)


class FraudEvaluationResponse(BaseModel):
    score: int = Field(ge=0, le=100)
    decision: RiskDecision
    engine_version: str
    signals: list[FraudSignalRead]


class FraudAssessmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    score: int
    decision: RiskDecision
    engine_version: str
    signals: list[dict[str, Any]]
    created_at: datetime
