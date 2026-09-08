"""Modelo de evaluación de riesgo."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.schemas.enums import RiskDecision
from app.schemas.fraud import FraudEvaluationResponse

if TYPE_CHECKING:
    from app.db.models.payment import Payment


class FraudAssessment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Resultado inmutable de una ejecución del motor de reglas.

    Se guarda una fila por evaluación (nunca se actualiza) para poder auditar
    por qué se bloqueó una operación y reproducir decisiones pasadas.
    """

    __tablename__ = "fraud_assessments"

    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), index=True
    )
    score: Mapped[int] = mapped_column(Integer)
    decision: Mapped[RiskDecision] = mapped_column(Enum(RiskDecision, native_enum=False, length=32))
    engine_version: Mapped[str] = mapped_column(String(32))
    signals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    payment: Mapped[Payment] = relationship(back_populates="assessments")

    @classmethod
    def from_evaluation(cls, evaluation: FraudEvaluationResponse) -> FraudAssessment:
        """Construye la fila auditable a partir del resultado del motor."""
        return cls(
            score=evaluation.score,
            decision=evaluation.decision,
            engine_version=evaluation.engine_version,
            signals=[signal.model_dump() for signal in evaluation.signals],
        )
