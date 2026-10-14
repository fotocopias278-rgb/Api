"""Alta de comercios y gestión de sus claves de API."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import generate_api_key, hash_api_key, key_prefix
from app.db.models import ApiKey, Merchant

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClaveEmitida:
    """Resultado de emitir una clave: el valor en claro solo existe aquí."""

    api_key: ApiKey
    secreto: str


class MerchantService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_merchant(self, name: str, *, meta: dict | None = None) -> Merchant:
        merchant = Merchant(name=name.strip(), meta=meta or {})
        self._session.add(merchant)
        await self._session.commit()
        await self._session.refresh(merchant)
        logger.info("merchant.created", extra={"merchant_id": str(merchant.id)})
        return merchant

    async def issue_key(self, merchant_id: uuid.UUID, *, name: str = "default") -> ClaveEmitida:
        """Emite una clave nueva.

        El valor en claro se devuelve una sola vez y no vuelve a existir: en la
        base solo queda su huella. Perderla significa emitir otra.
        """
        merchant = await self._session.get(Merchant, merchant_id)
        if merchant is None:
            raise NotFoundError(f"No existe el comercio {merchant_id}.")

        secreto = generate_api_key()
        api_key = ApiKey(
            merchant_id=merchant.id,
            name=name,
            prefix=key_prefix(secreto),
            key_hash=hash_api_key(secreto),
        )
        self._session.add(api_key)
        await self._session.commit()
        await self._session.refresh(api_key)

        logger.info(
            "api_key.issued",
            extra={"merchant_id": str(merchant.id), "key_id": str(api_key.id)},
        )
        return ClaveEmitida(api_key=api_key, secreto=secreto)

    async def revoke_key(self, merchant_id: uuid.UUID, key_id: uuid.UUID) -> ApiKey:
        api_key = await self._session.get(ApiKey, key_id)
        if api_key is None or api_key.merchant_id != merchant_id:
            raise NotFoundError(f"No existe la clave {key_id}.")
        if api_key.revoked_at is not None:
            raise ConflictError("La clave ya estaba revocada.")

        api_key.revoked_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(api_key)
        logger.info("api_key.revoked", extra={"key_id": str(key_id)})
        return api_key

    async def resolve_key(self, secreto: str) -> Merchant | None:
        """Traduce una clave en claro al comercio al que pertenece.

        Se busca por la huella —no por el valor— así que una filtración de la
        base de datos no entrega credenciales utilizables. El índice único
        sobre `key_hash` hace la consulta O(1), sin recorrer claves.
        """
        resultado = await self._session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(secreto))
        )
        api_key = resultado.scalar_one_or_none()
        if api_key is None or not api_key.is_active:
            return None
        if not api_key.merchant.is_active:
            logger.warning(
                "auth.merchant_inactivo", extra={"merchant_id": str(api_key.merchant_id)}
            )
            return None

        api_key.last_used_at = datetime.now(UTC)
        await self._session.commit()
        return api_key.merchant

    async def get_merchant(self, merchant_id: uuid.UUID) -> Merchant:
        merchant = await self._session.get(Merchant, merchant_id)
        if merchant is None:
            raise NotFoundError(f"No existe el comercio {merchant_id}.")
        return merchant

    async def list_merchants(self, *, limit: int = 50, offset: int = 0) -> list[Merchant]:
        filas = await self._session.execute(
            select(Merchant).order_by(Merchant.created_at.desc()).limit(limit).offset(offset)
        )
        return list(filas.scalars().all())
