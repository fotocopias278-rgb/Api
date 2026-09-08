"""Limitador de tasa: semántica del cubo de fichas sobre Redis."""

import asyncio

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import Settings
from app.services.rate_limit import RateLimiter, identity_from


def ajustes(**overrides) -> Settings:
    base = {
        "rate_limit_enabled": True,
        "rate_limit_per_minute": 60,  # 1 ficha por segundo
        "rate_limit_burst": 5,
        "rate_limit_fail_open": True,
    }
    return Settings(**{**base, **overrides})


@pytest.fixture
def limiter(redis_client, reloj):
    return RateLimiter(redis_client, ajustes(), clock=reloj)


async def test_dentro_del_cupo_se_permite(limiter):
    result = await limiter.check("cliente-1")
    assert result.allowed
    assert result.limit == 60
    assert result.remaining == 4  # capacidad 5, consumida 1


async def test_la_rafaga_se_agota_y_luego_se_bloquea(limiter):
    for _ in range(5):
        assert (await limiter.check("cliente-1")).allowed

    bloqueado = await limiter.check("cliente-1")
    assert not bloqueado.allowed
    assert bloqueado.remaining == 0
    assert bloqueado.retry_after_seconds >= 1


async def test_el_cubo_se_recarga_con_el_tiempo(limiter, reloj):
    for _ in range(5):
        await limiter.check("cliente-1")
    assert not (await limiter.check("cliente-1")).allowed

    # A 1 ficha/s, tras 3 s hay 3 fichas disponibles.
    reloj.avanzar(3)
    for _ in range(3):
        assert (await limiter.check("cliente-1")).allowed
    assert not (await limiter.check("cliente-1")).allowed


async def test_la_recarga_no_supera_la_capacidad(limiter, reloj):
    await limiter.check("cliente-1")
    # Una hora parado no da más fichas que la capacidad del cubo.
    reloj.avanzar(3_600)

    for _ in range(5):
        assert (await limiter.check("cliente-1")).allowed
    assert not (await limiter.check("cliente-1")).allowed


async def test_cada_identidad_tiene_su_cubo(limiter):
    for _ in range(5):
        await limiter.check("cliente-1")

    assert not (await limiter.check("cliente-1")).allowed
    # El vecino no paga el consumo ajeno.
    assert (await limiter.check("cliente-2")).allowed


async def test_el_coste_vacia_el_cubo_mas_rapido(limiter):
    """Una petición cara consume varias fichas de golpe."""
    assert (await limiter.check("cliente-1", cost=5)).allowed
    assert not (await limiter.check("cliente-1", cost=1)).allowed


async def test_una_peticion_mas_cara_que_el_cubo_nunca_pasa(limiter):
    result = await limiter.check("cliente-1", cost=99)
    assert not result.allowed


async def test_el_reloj_hacia_atras_no_regala_fichas(limiter, reloj):
    """Un salto de reloj hacia atrás no debe recargar el cubo."""
    for _ in range(5):
        await limiter.check("cliente-1")

    reloj.avanzar(-600)
    assert not (await limiter.check("cliente-1")).allowed


async def test_peticiones_concurrentes_no_se_pasan_del_cupo(redis_client, reloj):
    """La prueba real de atomicidad: 50 peticiones a la vez, 5 fichas.

    Sin el script Lua, varias corrutinas leerían el mismo saldo y todas
    pasarían. Con él, exactamente `capacidad` peticiones son admitidas.
    """
    limiter = RateLimiter(redis_client, ajustes(rate_limit_burst=5), clock=reloj)

    resultados = await asyncio.gather(*(limiter.check("concurrente") for _ in range(50)))
    permitidas = sum(1 for r in resultados if r.allowed)

    assert permitidas == 5


async def test_la_clave_caduca_sola(limiter, redis_client):
    """El cubo no se queda en Redis para siempre."""
    await limiter.check("cliente-efimero")
    ttl = await redis_client.pttl("rl:cliente-efimero")
    assert 0 < ttl <= (5 / 1.0) * 1000 + 1000


async def test_desactivado_deja_pasar_todo(redis_client, reloj):
    limiter = RateLimiter(redis_client, ajustes(rate_limit_enabled=False), clock=reloj)
    for _ in range(50):
        assert (await limiter.check("cliente-1")).allowed


async def test_sin_backend_no_limita(reloj):
    """Sin cliente Redis el limitador queda inerte, no revienta."""
    limiter = RateLimiter(None, ajustes(), clock=reloj)
    assert not limiter.enabled
    assert (await limiter.check("cliente-1")).allowed


# --- Comportamiento ante una caída de Redis ---------------------------------


class RedisCaido:
    """Doble que falla como lo haría un Redis inaccesible."""

    async def script_load(self, _script):
        raise RedisConnectionError("Error 111 connecting to redis")

    async def evalsha(self, *_args):
        raise RedisConnectionError("Error 111 connecting to redis")


async def test_fail_open_deja_pasar_si_redis_cae(reloj):
    limiter = RateLimiter(RedisCaido(), ajustes(rate_limit_fail_open=True), clock=reloj)
    result = await limiter.check("cliente-1")
    assert result.allowed  # se degrada, no se cae


async def test_fail_closed_rechaza_si_redis_cae(reloj):
    limiter = RateLimiter(RedisCaido(), ajustes(rate_limit_fail_open=False), clock=reloj)
    result = await limiter.check("cliente-1")
    assert not result.allowed


async def test_se_recupera_si_redis_pierde_el_script(limiter, redis_client):
    """Tras un reinicio de Redis el EVALSHA falla con NOSCRIPT y se reenvía."""
    assert (await limiter.check("cliente-1")).allowed

    await redis_client.script_flush()  # simula el reinicio
    assert (await limiter.check("cliente-1")).allowed


# --- Derivación de la identidad ---------------------------------------------


def test_la_identidad_prefiere_la_clave_de_api():
    identidad = identity_from("clave-secreta", "10.0.0.1")
    assert identidad.startswith("key:")
    # La clave nunca viaja en claro a Redis.
    assert "clave-secreta" not in identidad


def test_la_identidad_cae_a_la_ip_sin_clave():
    assert identity_from(None, "10.0.0.1") == "ip:10.0.0.1"


def test_claves_distintas_dan_identidades_distintas():
    assert identity_from("clave-a", None) != identity_from("clave-b", None)


def test_sin_clave_ni_ip():
    assert identity_from(None, None) == "ip:desconocida"
