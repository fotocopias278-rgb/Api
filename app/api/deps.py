"""Dependencias compartidas por los endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Query, Request
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError
from app.core.security import ADMIN_KEY_HEADER, API_KEY_HEADER, verify_admin_key
from app.db.models import Merchant
from app.db.session import SessionFactory
from app.services.merchants import MerchantService
from app.services.payments import PaymentService
from app.services.receipts import ReceiptService

api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)
admin_key_scheme = APIKeyHeader(name=ADMIN_KEY_HEADER, auto_error=False)


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


async def require_merchant(
    session: SessionDep,
    api_key: Annotated[str | None, Depends(api_key_scheme)] = None,
) -> Merchant:
    """Resuelve la clave de API al comercio dueño de los datos.

    Devolver el comercio —y no la clave— es lo que hace imposible olvidarse de
    filtrar: los servicios lo exigen en el constructor, así que una consulta
    sin inquilino no compila conceptualmente, falla al instanciarse.
    """
    if not api_key:
        raise AuthenticationError("Falta la cabecera X-API-Key.")

    merchant = await MerchantService(session).resolve_key(api_key)
    if merchant is None:
        raise AuthenticationError("Clave de API inválida o revocada.")
    return merchant


MerchantDep = Annotated[Merchant, Depends(require_merchant)]


async def require_admin(
    settings: SettingsDep,
    admin_key: Annotated[str | None, Depends(admin_key_scheme)] = None,
) -> str:
    """Protege las rutas de administración de la plataforma."""
    if not verify_admin_key(admin_key, settings):
        raise AuthenticationError("Clave de administración ausente o inválida.")
    return admin_key or ""


AdminDep = Annotated[str, Depends(require_admin)]


def get_payment_service(
    session: SessionDep, settings: SettingsDep, merchant: MerchantDep
) -> PaymentService:
    return PaymentService(session, merchant=merchant, config=settings)


def get_receipt_service(
    session: SessionDep, settings: SettingsDep, merchant: MerchantDep
) -> ReceiptService:
    return ReceiptService(session, merchant=merchant, config=settings)


def get_merchant_service(session: SessionDep) -> MerchantService:
    return MerchantService(session)


PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]
ReceiptServiceDep = Annotated[ReceiptService, Depends(get_receipt_service)]
MerchantServiceDep = Annotated[MerchantService, Depends(get_merchant_service)]

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
