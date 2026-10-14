"""Consulta de consumo: el comercio ve el suyo; el administrador, el de todos."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import AdminDep, MerchantDep, SessionDep
from app.schemas.usage import UsageSummary
from app.services.usage import UsageService

router = APIRouter(tags=["consumo"])

Desde = Annotated[datetime | None, Query(description="Inicio del periodo (inclusive).")]
Hasta = Annotated[datetime | None, Query(description="Fin del periodo (exclusive).")]


@router.get("/usage", response_model=UsageSummary, summary="Consumo propio")
async def my_usage(
    session: SessionDep, merchant: MerchantDep, desde: Desde = None, hasta: Hasta = None
) -> UsageSummary:
    """Consumo del comercio autenticado, para que concilie su factura."""
    totales = await UsageService(session).summary(merchant.id, desde=desde, hasta=hasta)
    return UsageSummary(merchant_id=merchant.id, desde=desde, hasta=hasta, totals=totales)


@router.get(
    "/admin/merchants/{merchant_id}/usage",
    response_model=UsageSummary,
    summary="Consumo de un comercio",
)
async def merchant_usage(
    merchant_id: Annotated[uuid.UUID, Path(description="Comercio a consultar.")],
    session: SessionDep,
    _: AdminDep,
    desde: Desde = None,
    hasta: Hasta = None,
) -> UsageSummary:
    totales = await UsageService(session).summary(merchant_id, desde=desde, hasta=hasta)
    return UsageSummary(merchant_id=merchant_id, desde=desde, hasta=hasta, totals=totales)
