"""Evaluación de riesgo sin persistencia."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import PaymentServiceDep
from app.schemas.fraud import FraudEvaluationRequest, FraudEvaluationResponse
from app.schemas.payment import PaymentCreate
from app.services.fraud import fraud_engine

router = APIRouter(prefix="/fraud", tags=["fraude"])


@router.post(
    "/evaluate",
    response_model=FraudEvaluationResponse,
    summary="Puntuar una operación sin cobrarla",
)
@router.post(
    "/score",
    response_model=FraudEvaluationResponse,
    summary="Alias de POST /fraud/evaluate",
    description=(
        "Idéntico a `POST /fraud/evaluate`. Existe para el contrato maestro; "
        "ambas rutas comparten manejador, así que no pueden divergir."
    ),
)
async def evaluate(
    payload: FraudEvaluationRequest, service: PaymentServiceDep
) -> FraudEvaluationResponse:
    """Devuelve la puntuación de riesgo que tendría la operación.

    Útil para decidir antes de cobrar: usa los mismos agregados históricos y las
    mismas reglas que `POST /payments`, pero no crea ningún pago.
    """
    context = await service.build_fraud_context(
        PaymentCreate(
            customer_id=payload.customer_id,
            amount_minor=payload.amount_minor,
            currency=payload.currency,
            email=payload.email,
            card_bin=payload.card_bin,
            card_country=payload.card_country,
            ip_country=payload.ip_country,
        )
    )
    return fraud_engine.evaluate(context)
