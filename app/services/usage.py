"""Medición de uso por comercio, para facturar."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.usage import UsageEvent, UsageKind

logger = logging.getLogger(__name__)


class UsageService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        merchant_id: uuid.UUID,
        kind: str,
        *,
        quantity: int = 1,
        meta: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> None:
        """Anota un hecho facturable.

        `commit=False` permite colgarlo de la transacción de quien llama, para
        que el hecho y lo que lo provocó se guarden o se pierdan juntos.
        """
        self._session.add(
            UsageEvent(
                merchant_id=merchant_id, kind=kind, quantity=quantity, meta=meta or {}
            )
        )
        if commit:
            await self._session.commit()

    async def summary(
        self,
        merchant_id: uuid.UUID,
        *,
        desde: datetime | None = None,
        hasta: datetime | None = None,
    ) -> dict[str, int]:
        """Totales por tipo de hecho en el periodo, para emitir una factura."""
        filtros = [UsageEvent.merchant_id == merchant_id]
        if desde is not None:
            filtros.append(UsageEvent.created_at >= desde)
        if hasta is not None:
            filtros.append(UsageEvent.created_at < hasta)

        filas = await self._session.execute(
            select(UsageEvent.kind, func.sum(UsageEvent.quantity))
            .where(*filtros)
            .group_by(UsageEvent.kind)
        )
        totales = {kind: int(total or 0) for kind, total in filas.all()}
        # Los tipos sin uso aparecen a cero: una factura no debe tener huecos.
        for kind in (
            UsageKind.PAYMENT_CREATED,
            UsageKind.RECEIPT_UPLOADED,
            UsageKind.VISION_EXTRACTION,
        ):
            totales.setdefault(kind, 0)
        return totales
