"""Esquemas de comprobantes bancarios."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import ReceiptStatus, ReconciliationStatus
from app.schemas.vision import ReceiptExtraction


class ReceiptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    payment_reference: str | None = None
    filename: str
    content_type: str
    size_bytes: int
    content_sha256: str
    status: ReceiptStatus
    reconciliation_status: ReconciliationStatus
    extraction: ReceiptExtraction | None = None
    confidence: float | None = None
    reconciliation_notes: list[str] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ReceiptAccepted(BaseModel):
    """Respuesta 202: el análisis continúa en segundo plano."""

    id: uuid.UUID
    status: ReceiptStatus
    task_id: str | None = None
    duplicate_of: uuid.UUID | None = Field(
        default=None, description="Comprobante previo con idéntico contenido."
    )


class ReconciliationResult(BaseModel):
    status: ReconciliationStatus
    notes: list[str] = Field(default_factory=list)
