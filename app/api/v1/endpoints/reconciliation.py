"""Endpoints para generar y consultar informes de conciliación."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, status

from app.api.deps import MerchantDep, SessionDep
from app.schemas.reconciliation import ReconciliationJobCreate, ReconciliationJobRead
from app.services.reconciliation_jobs import ReconciliationJobService

router = APIRouter(prefix="/reconciliation/jobs", tags=["conciliación"])


@router.post(
    "",
    response_model=ReconciliationJobRead,
    status_code=status.HTTP_201_CREATED,
    summary="Generar informe de conciliación por periodo",
)
async def create_reconciliation_job(
    payload: ReconciliationJobCreate,
    session: SessionDep,
    merchant: MerchantDep,
) -> ReconciliationJobRead:
    job = await ReconciliationJobService(session, merchant=merchant).create(
        payload.period_start, payload.period_end
    )
    return ReconciliationJobRead.model_validate(job)


@router.get(
    "/{job_id}",
    response_model=ReconciliationJobRead,
    summary="Consultar un informe de conciliación",
)
async def get_reconciliation_job(
    job_id: Annotated[uuid.UUID, Path(description="Identificador del trabajo.")],
    session: SessionDep,
    merchant: MerchantDep,
) -> ReconciliationJobRead:
    job = await ReconciliationJobService(session, merchant=merchant).get(job_id)
    return ReconciliationJobRead.model_validate(job)
