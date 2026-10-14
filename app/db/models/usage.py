"""Registro de uso facturable."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UsageEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Un hecho facturable, inmutable.

    Se guarda el hecho y no un contador agregado: un contador que se
    incrementa no se puede auditar ni recalcular, y si el cliente discute una
    factura no hay con qué responderle. Con los hechos, cualquier total se
    reconstruye.
    """

    __tablename__ = "usage_events"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(48), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    __table_args__ = (
        # La facturación siempre pregunta "este comercio, este periodo".
        Index("ix_usage_merchant_created", "merchant_id", "created_at"),
    )


class UsageKind:
    """Hechos facturables. Son cadenas y no un enum para poder añadir sin migrar.

    No se anota cada petición HTTP: sería una escritura en base de datos por
    petición, duplicando la carga para medir algo que no cuesta dinero. Se mide
    lo que sí lo cuesta —un cobro, un fichero almacenado, una llamada al
    modelo—; el volumen bruto de peticiones ya lo acota el limitador de tasa.
    """

    PAYMENT_CREATED = "payment_created"
    RECEIPT_UPLOADED = "receipt_uploaded"
    VISION_EXTRACTION = "vision_extraction"
