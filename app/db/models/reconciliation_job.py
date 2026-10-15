"""Trabajos de conciliación por lotes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.schemas.enums import JobStatus


class ReconciliationJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Informe de conciliación de un comercio en un periodo.

    Se persiste el resultado completo —contadores y detalle— en vez de
    recalcularlo al consultar: un informe es una foto de un momento, y si los
    datos cambian después el informe debe seguir diciendo lo que dijo.
    """

    __tablename__ = "reconciliation_jobs"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=32), default=JobStatus.PENDING, index=True
    )

    total_payments: Mapped[int] = mapped_column(Integer, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, default=0)
    mismatched_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    unmatched_receipts_count: Mapped[int] = mapped_column(Integer, default=0)

    discrepancies: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(String(512), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (Index("ix_jobs_merchant_created", "merchant_id", "created_at"),)
