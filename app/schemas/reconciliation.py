"""Esquemas de los trabajos de conciliación por periodo."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.schemas.enums import DiscrepancyKind, JobStatus


class ReconciliationJobCreate(BaseModel):
    period_start: datetime
    period_end: datetime

    @field_validator("period_start", "period_end")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Las fechas del periodo deben incluir zona horaria.")
        return value

    @model_validator(mode="after")
    def _valid_period(self) -> ReconciliationJobCreate:
        if self.period_start >= self.period_end:
            raise ValueError("period_start debe ser anterior a period_end.")
        if self.period_end - self.period_start > timedelta(days=92):
            raise ValueError("El periodo de conciliación no puede superar 92 días.")
        return self


class DiscrepancyRead(BaseModel):
    kind: DiscrepancyKind
    payment_reference: str | None = None
    receipt_id: uuid.UUID | None = None
    webhook_event_id: str | None = None
    detail: str


class ReconciliationJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    period_start: datetime
    period_end: datetime
    status: JobStatus
    total_payments: int
    matched_count: int
    mismatched_count: int
    pending_count: int
    duplicate_count: int
    unmatched_receipts_count: int
    discrepancies: list[DiscrepancyRead]
    error_message: str | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
