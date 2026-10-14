"""Esquemas de consumo."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class UsageSummary(BaseModel):
    """Totales facturables de un comercio en un periodo."""

    merchant_id: uuid.UUID
    desde: datetime | None = None
    hasta: datetime | None = None
    totals: dict[str, int] = Field(
        description="Unidades por tipo de hecho: cobros, comprobantes y extracciones."
    )
