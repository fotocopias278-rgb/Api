"""Endpoints de pagos."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Path, Query, status

from app.api.deps import IdempotencyKey, PaginationDep, PaymentServiceDep
from app.core.exceptions import NotFoundError
from app.schemas.common import Page
from app.schemas.enums import PaymentStatus
from app.schemas.payment import PaymentCreate, PaymentDetail, PaymentRead, RefundRequest

router = APIRouter(prefix="/payments", tags=["pagos"])

ReferencePath = Annotated[str, Path(description="Referencia pública del pago.", max_length=64)]


@router.post(
    "",
    response_model=PaymentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear y evaluar un pago",
)
async def create_payment(
    payload: PaymentCreate,
    service: PaymentServiceDep,
    idempotency_key: IdempotencyKey = None,
) -> PaymentRead:
    """Crea el pago, lo puntúa y lo envía a la pasarela si el riesgo lo permite.

    Un bloqueo por riesgo **no** es un error HTTP: la respuesta es 201 y el
    veredicto viaja en `status` (`rejected`, `under_review`) junto con
    `risk_score` y `risk_decision`.
    """
    payment = await service.create_payment(payload, idempotency_key=idempotency_key)
    return PaymentRead.model_validate(payment)


@router.get("", response_model=Page[PaymentRead], summary="Listar pagos")
async def list_payments(
    service: PaymentServiceDep,
    pagination: PaginationDep,
    customer_id: Annotated[str | None, Query(max_length=64)] = None,
    payment_status: Annotated[PaymentStatus | None, Query(alias="status")] = None,
) -> Page[PaymentRead]:
    items, total = await service.list_payments(
        customer_id=customer_id,
        status=payment_status,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page[PaymentRead](
        items=[PaymentRead.model_validate(item) for item in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{reference}", response_model=PaymentDetail, summary="Consultar un pago")
async def get_payment(
    reference: ReferencePath, service: PaymentServiceDep
) -> PaymentDetail:
    """Devuelve el pago junto con su historial de evaluaciones de riesgo."""
    payment = await service.get_by_reference(reference)
    if payment is None:
        raise NotFoundError(f"No existe el pago {reference}.")
    return PaymentDetail.model_validate(payment)


@router.post("/{reference}/refund", response_model=PaymentRead, summary="Devolver un pago")
async def refund_payment(
    reference: ReferencePath,
    service: PaymentServiceDep,
    payload: RefundRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> PaymentRead:
    body = payload or RefundRequest()
    payment = await service.refund(
        reference,
        amount_minor=body.amount_minor,
        reason=body.reason,
        idempotency_key=idempotency_key,
    )
    return PaymentRead.model_validate(payment)


@router.post(
    "/{reference}/review",
    response_model=PaymentRead,
    summary="Resolver un pago en revisión manual",
)
async def review_payment(
    reference: ReferencePath,
    service: PaymentServiceDep,
    approve: Annotated[
        bool, Body(embed=True, description="True para cobrar, False para rechazar.")
    ],
) -> PaymentRead:
    """Cierra manualmente un pago retenido por el motor de riesgo."""
    payment = await service.mark_under_review_decision(reference, approve)
    return PaymentRead.model_validate(payment)
