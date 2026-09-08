"""Dependencias compartidas por los endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Query, Request
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError
from app.core.security import API_KEY_HEADER, verify_api_key
from app.db.session import SessionFactory
from app.services.payments import PaymentService
from app.services.receipts import ReceiptService

api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Sesión de base de datos por petición."""
    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def require_api_key(
    settings: SettingsDep,
    api_key: Annotated[str | None, Depends(api_key_scheme)] = None,
) -> str:
    """Rechaza la petición si la clave de API no es válida."""
    if not verify_api_key(api_key, settings):
        raise AuthenticationError("Clave de API ausente o inválida.")
    return api_key or ""


AuthDep = Annotated[str, Depends(require_api_key)]


def get_payment_service(session: SessionDep, settings: SettingsDep) -> PaymentService:
    return PaymentService(session, config=settings)


def get_receipt_service(session: SessionDep, settings: SettingsDep) -> ReceiptService:
    return ReceiptService(session, config=settings)


PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]
ReceiptServiceDep = Annotated[ReceiptService, Depends(get_receipt_service)]

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description="Reintentar con la misma clave devuelve el pago original.",
        max_length=128,
    ),
]


class Pagination:
    """Parámetros de paginación por desplazamiento."""

    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> None:
        self.limit = limit
        self.offset = offset


PaginationDep = Annotated[Pagination, Depends(Pagination)]


def get_request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)
