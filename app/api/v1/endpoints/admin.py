"""Administración de la plataforma: alta de comercios y de sus claves."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, status

from app.api.deps import AdminDep, MerchantServiceDep, PaginationDep
from app.schemas.merchant import (
    ApiKeyCreate,
    ApiKeyIssued,
    ApiKeyRead,
    MerchantCreate,
    MerchantRead,
)

router = APIRouter(prefix="/admin", tags=["administración"])

MerchantId = Annotated[uuid.UUID, Path(description="Identificador del comercio.")]


@router.post(
    "/merchants",
    response_model=MerchantRead,
    status_code=status.HTTP_201_CREATED,
    summary="Dar de alta un comercio",
)
async def create_merchant(
    payload: MerchantCreate, service: MerchantServiceDep, _: AdminDep
) -> MerchantRead:
    merchant = await service.create_merchant(payload.name, meta=payload.meta)
    return MerchantRead.model_validate(merchant)


@router.get("/merchants", response_model=list[MerchantRead], summary="Listar comercios")
async def list_merchants(
    service: MerchantServiceDep, pagination: PaginationDep, _: AdminDep
) -> list[MerchantRead]:
    comercios = await service.list_merchants(limit=pagination.limit, offset=pagination.offset)
    return [MerchantRead.model_validate(m) for m in comercios]


@router.post(
    "/merchants/{merchant_id}/keys",
    response_model=ApiKeyIssued,
    status_code=status.HTTP_201_CREATED,
    summary="Emitir una clave de API",
)
async def issue_key(
    merchant_id: MerchantId,
    payload: ApiKeyCreate,
    service: MerchantServiceDep,
    _: AdminDep,
) -> ApiKeyIssued:
    """Emite una clave para el comercio.

    **El secreto se devuelve una sola vez.** En la base solo queda su huella,
    así que no hay forma de volver a mostrarlo: si se pierde, se revoca y se
    emite otro.
    """
    emitida = await service.issue_key(merchant_id, name=payload.name)
    return ApiKeyIssued(
        **ApiKeyRead.model_validate(emitida.api_key).model_dump(), secret=emitida.secreto
    )


@router.get(
    "/merchants/{merchant_id}/keys",
    response_model=list[ApiKeyRead],
    summary="Listar las claves de un comercio",
)
async def list_keys(
    merchant_id: MerchantId, service: MerchantServiceDep, _: AdminDep
) -> list[ApiKeyRead]:
    merchant = await service.get_merchant(merchant_id)
    return [ApiKeyRead.model_validate(k) for k in merchant.api_keys]


@router.delete(
    "/merchants/{merchant_id}/keys/{key_id}",
    response_model=ApiKeyRead,
    summary="Revocar una clave",
)
async def revoke_key(
    merchant_id: MerchantId,
    key_id: Annotated[uuid.UUID, Path(description="Identificador de la clave.")],
    service: MerchantServiceDep,
    _: AdminDep,
) -> ApiKeyRead:
    api_key = await service.revoke_key(merchant_id, key_id)
    return ApiKeyRead.model_validate(api_key)
