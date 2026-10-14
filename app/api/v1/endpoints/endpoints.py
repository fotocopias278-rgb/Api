"""Gestión por el comercio de sus destinos de webhook."""

from __future__ import annotations

import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Path, status
from sqlalchemy import select

from app.api.deps import MerchantDep, SessionDep
from app.core.exceptions import NotFoundError
from app.db.models.endpoint import WebhookEndpoint
from app.schemas.endpoint import WebhookEndpointCreate, WebhookEndpointRead

router = APIRouter(prefix="/webhook-endpoints", tags=["webhooks salientes"])

EndpointId = Annotated[uuid.UUID, Path(description="Identificador del destino.")]


@router.post(
    "",
    response_model=WebhookEndpointRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar un destino de webhook",
)
async def create_endpoint(
    payload: WebhookEndpointCreate, session: SessionDep, merchant: MerchantDep
) -> WebhookEndpointRead:
    """Registra una URL a la que avisar.

    El secreto se genera aquí si no lo aportas, y **sí se puede consultar
    después**: a diferencia de una clave de API, no da acceso a nada — sirve
    para que verifiques que el aviso viene de nosotros.
    """
    destino = WebhookEndpoint(
        merchant_id=merchant.id,
        url=str(payload.url),
        secret=payload.secret or f"whsec_{secrets.token_urlsafe(24)}",
        event_types=payload.event_types,
    )
    session.add(destino)
    await session.commit()
    await session.refresh(destino)
    return WebhookEndpointRead.model_validate(destino)


@router.get("", response_model=list[WebhookEndpointRead], summary="Listar destinos")
async def list_endpoints(
    session: SessionDep, merchant: MerchantDep
) -> list[WebhookEndpointRead]:
    filas = await session.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.merchant_id == merchant.id)
    )
    return [WebhookEndpointRead.model_validate(e) for e in filas.scalars()]


@router.delete(
    "/{endpoint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar un destino",
)
async def delete_endpoint(
    endpoint_id: EndpointId, session: SessionDep, merchant: MerchantDep
) -> None:
    destino = await session.scalar(
        select(WebhookEndpoint).where(
            WebhookEndpoint.merchant_id == merchant.id, WebhookEndpoint.id == endpoint_id
        )
    )
    if destino is None:
        raise NotFoundError(f"No existe el destino {endpoint_id}.")
    await session.delete(destino)
    await session.commit()
