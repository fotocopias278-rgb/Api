"""Limitación de tasa con Redis (algoritmo de cubo de fichas).

El cubo de fichas permite ráfagas cortas sin dejar de acotar el ritmo sostenido,
que es justo lo que quiere una API de pagos: un cliente puede enviar un lote
puntual, pero no martillear el endpoint indefinidamente.

Todo el cálculo vive en un script Lua porque leer el estado, recargar el cubo y
consumir la ficha debe ser **una sola operación atómica**: con varias llamadas
sueltas, dos peticiones simultáneas leerían el mismo saldo y ambas pasarían.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.security import sha256_hexdigest

logger = logging.getLogger(__name__)

# KEYS[1] = clave del cubo
# ARGV = [ritmo (fichas/s), capacidad, ahora (s), coste, ttl (ms)]
# Devuelve: [permitido, fichas restantes, espera en ms]
TOKEN_BUCKET_LUA = """
local key      = KEYS[1]
local rate     = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now      = tonumber(ARGV[3])
local cost     = tonumber(ARGV[4])
local ttl_ms   = tonumber(ARGV[5])

local bucket = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local ts     = tonumber(bucket[2])

if tokens == nil or ts == nil then
    tokens = capacity
    ts = now
end

-- Recarga proporcional al tiempo transcurrido, sin superar la capacidad.
local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * rate)

local allowed = 0
if tokens >= cost then
    tokens = tokens - cost
    allowed = 1
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', key, ttl_ms)

local retry_ms = 0
if allowed == 0 then
    retry_ms = math.ceil(((cost - tokens) / rate) * 1000)
end

return {allowed, math.floor(tokens), retry_ms}
"""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int

    @property
    def retry_after_header(self) -> str:
        # `Retry-After` va en segundos enteros y nunca puede ser 0: un cliente
        # que reintente de inmediato volvería a chocar contra el límite.
        return str(max(1, self.retry_after_seconds))


class RateLimiter:
    """Cubo de fichas por identidad, respaldado por Redis."""

    def __init__(
        self,
        redis: Redis | None,
        config: Settings | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._settings = config or default_settings
        self._clock = clock
        self._script_sha: str | None = None

    @property
    def enabled(self) -> bool:
        return self._settings.rate_limit_enabled and self._redis is not None

    async def check(self, identity: str, *, cost: int = 1) -> RateLimitResult:
        """Consume `cost` fichas del cubo de `identity`."""
        limit = self._settings.rate_limit_per_minute
        capacity = self._settings.rate_limit_burst or limit

        if not self.enabled:
            return RateLimitResult(True, limit, capacity, 0)

        rate = limit / 60.0
        # Vida de la clave: lo que tarda el cubo en llenarse del todo, más un
        # margen. Pasado ese tiempo el cubo está lleno y da igual olvidarlo.
        ttl_ms = int((capacity / rate) * 1000) + 1000

        try:
            allowed, remaining, retry_ms = await self._eval(
                f"rl:{identity}", rate, capacity, self._clock(), cost, ttl_ms
            )
        except RedisError as exc:
            return self._on_backend_failure(exc, limit, capacity)

        return RateLimitResult(
            allowed=bool(allowed),
            limit=limit,
            remaining=int(remaining),
            retry_after_seconds=int(retry_ms // 1000) + (1 if retry_ms % 1000 else 0),
        )

    async def _eval(
        self, key: str, rate: float, capacity: int, now: float, cost: int, ttl_ms: int
    ) -> list[int]:
        assert self._redis is not None
        args = [rate, capacity, now, cost, ttl_ms]

        # Se cachea el SHA para enviar EVALSHA en vez del script entero.
        if self._script_sha is None:
            self._script_sha = await self._redis.script_load(TOKEN_BUCKET_LUA)
        try:
            return await self._redis.evalsha(self._script_sha, 1, key, *args)
        except RedisError as exc:
            # Redis reiniciado: el script ya no está en caché, se reenvía.
            if "NOSCRIPT" not in str(exc):
                raise
            self._script_sha = await self._redis.script_load(TOKEN_BUCKET_LUA)
            return await self._redis.evalsha(self._script_sha, 1, key, *args)

    def _on_backend_failure(self, exc: Exception, limit: int, capacity: int) -> RateLimitResult:
        """Decide qué hacer cuando Redis no responde.

        Por defecto se deja pasar (*fail-open*): quedarse sin limitador es malo,
        pero tumbar toda la pasarela de cobro porque el contador está caído lo es
        más. Quien prefiera lo contrario tiene `RATE_LIMIT_FAIL_OPEN=false`.
        """
        logger.error("rate_limit.backend_unavailable", extra={"error": str(exc)}, exc_info=True)
        if self._settings.rate_limit_fail_open:
            return RateLimitResult(True, limit, capacity, 0)
        return RateLimitResult(False, limit, 0, 1)


def identity_from(api_key: str | None, client_ip: str | None) -> str:
    """Deriva la identidad a limitar.

    Se prefiere la clave de API (identifica al integrador aunque cambie de IP) y
    se recurre a la IP para el tráfico sin autenticar. La clave nunca se guarda
    en claro en Redis: se almacena su huella.
    """
    if api_key:
        return f"key:{sha256_hexdigest(api_key.encode())[:32]}"
    return f"ip:{client_ip or 'desconocida'}"
