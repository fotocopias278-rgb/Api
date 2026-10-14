"""Configuración común de los tests.

Las variables de entorno se fijan **antes** de importar nada de `app`: la
configuración es un singleton que se construye al importarse el módulo.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Iterator

_TMP_DIR = tempfile.mkdtemp(prefix="finai-tests-")
_DB_PATH = os.path.join(_TMP_DIR, "test.db")

# Por defecto la suite corre sobre SQLite (rápida y sin dependencias), pero
# apuntando TEST_DATABASE_URL a un Postgres se ejecuta idéntica contra el motor
# real. Es la única forma de detectar lo que SQLite oculta —sobre todo las
# carreras, porque SQLite serializa las escrituras.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_DB_PATH}"
)
IS_POSTGRES = TEST_DATABASE_URL.startswith("postgresql")

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": TEST_DATABASE_URL,
        "ADMIN_API_KEYS": "clave-admin-de-test",
        "RECEIPT_STORAGE_DIR": os.path.join(_TMP_DIR, "receipts"),
        "STRIPE_WEBHOOK_SECRET": "whsec_test_secret",
        "FRAUD_BLOCKED_BINS": "666666",
        "LOG_LEVEL": "WARNING",
        # Se neutralizan las credenciales de los proveedores de IA: si un
        # desarrollador tiene su clave en `.env`, pydantic-settings la leería y
        # la suite acabaría haciendo llamadas de pago reales sin pretenderlo.
        "GOOGLE_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        # Cupo pequeño para poder agotarlo en los tests sin lanzar cientos de
        # peticiones. El cliente por defecto lleva el limitador desactivado.
        "RATE_LIMIT_PER_MINUTE": "60",
        "RATE_LIMIT_BURST": "5",
    }
)

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from redis.asyncio import from_url as redis_from_url  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.deps import get_session  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_api_key, key_prefix  # noqa: E402
from app.db import models  # noqa: E402,F401 - registra las tablas
from app.db.base import Base  # noqa: E402
from app.db.models import ApiKey, Merchant  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.rate_limit import RateLimiter  # noqa: E402

# Dos comercios con claves fijas: así las cabeceras pueden ser constantes y,
# sobre todo, cada test puede comprobar que uno no ve los datos del otro.
API_KEY = "clave-comercio-uno"
API_KEY_OTRO = "clave-comercio-dos"
AUTH_HEADERS = {"X-API-Key": API_KEY}
AUTH_HEADERS_OTRO = {"X-API-Key": API_KEY_OTRO}

ADMIN_KEY = "clave-admin-de-test"
ADMIN_HEADERS = {"X-Admin-Key": ADMIN_KEY}

MERCHANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
MERCHANT_ID_OTRO = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
async def engine():
    """Motor con el esquema recreado desde el metadata para cada test."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Los dos comercios de prueba, sembrados con el esquema.
    sembrar = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with sembrar() as session:
        for merchant_id, nombre, clave in (
            (MERCHANT_ID, "Comercio Uno", API_KEY),
            (MERCHANT_ID_OTRO, "Comercio Dos", API_KEY_OTRO),
        ):
            session.add(Merchant(id=merchant_id, name=nombre))
            session.add(
                ApiKey(
                    merchant_id=merchant_id,
                    name="test",
                    prefix=key_prefix(clave),
                    key_hash=hash_api_key(clave),
                )
            )
        await session.commit()

    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def session(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
async def client(session_factory, monkeypatch) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP contra la app, con la base de datos de test inyectada."""

    # El broker Redis no existe en los tests: se sustituye el encolado.
    monkeypatch.setattr(
        "app.api.v1.endpoints.receipts.enqueue_receipt_analysis",
        lambda receipt_id: "task-simulada",
    )

    app = create_app()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    # Sin limitador: el resto de la suite no debe depender de cupos.
    app.state.rate_limiter = RateLimiter(None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client

    app.dependency_overrides.clear()


# --- Redis para los tests de limitación de tasa -------------------------------

def _puerto_libre() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str | None]:
    """Proporciona un Redis real para la sesión de tests.

    Por orden: el que indique TEST_REDIS_URL, un `redis-server` efímero en un
    puerto libre, o `fakeredis` si no hay binario. Se prefiere Redis de verdad
    porque el limitador vive en un script Lua y sus garantías (atomicidad, TTL,
    recuperación de NOSCRIPT) solo se comprueban contra el servidor.
    """
    # Un Redis facilitado por el entorno (contenedor de CI) manda sobre todo lo
    # demás. Se exige la variable propia TEST_REDIS_URL en vez de reutilizar
    # REDIS_URL: estas pruebas hacen FLUSHDB, y vaciar por error el Redis de
    # desarrollo de alguien sería un estropicio difícil de perdonar.
    externo = os.environ.get("TEST_REDIS_URL")
    if externo:
        yield externo
        return

    binario = shutil.which("redis-server")
    if binario is None:
        yield None
        return

    puerto = _puerto_libre()
    directorio = tempfile.mkdtemp(prefix="finai-redis-")
    proceso = subprocess.Popen(
        [binario, "--port", str(puerto), "--save", "", "--appendonly", "no", "--dir", directorio],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    url = f"redis://127.0.0.1:{puerto}/0"
    for _ in range(100):  # hasta 10 s de arranque
        if proceso.poll() is not None:
            raise RuntimeError("redis-server terminó inesperadamente")
        try:
            with socket.create_connection(("127.0.0.1", puerto), timeout=0.1):
                break
        except OSError:
            time.sleep(0.1)
    else:
        proceso.kill()
        raise RuntimeError("redis-server no llegó a aceptar conexiones")

    try:
        yield url
    finally:
        proceso.terminate()
        proceso.wait(timeout=10)
        shutil.rmtree(directorio, ignore_errors=True)


@pytest.fixture
async def redis_client(redis_url):
    """Cliente Redis limpio para cada test (real o simulado)."""
    if redis_url is None:
        import fakeredis.aioredis

        client = fakeredis.aioredis.FakeRedis()
    else:
        client = redis_from_url(redis_url)

    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
def reloj():
    """Reloj controlable: permite avanzar el tiempo sin dormir el test."""

    class Reloj:
        def __init__(self) -> None:
            self.ahora = 1_000_000.0

        def __call__(self) -> float:
            return self.ahora

        def avanzar(self, segundos: float) -> None:
            self.ahora += segundos

    return Reloj()


@pytest.fixture
async def client_limitado(session_factory, redis_client, monkeypatch, settings):
    """Cliente HTTP con el limitador activo y respaldado por Redis."""
    monkeypatch.setattr(
        "app.api.v1.endpoints.receipts.enqueue_receipt_analysis",
        lambda receipt_id: "task-simulada",
    )

    app = create_app()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    # `ASGITransport` no ejecuta el lifespan, así que el estado que allí se
    # prepara hay que inyectarlo a mano.
    app.state.redis = redis_client
    app.state.rate_limiter = RateLimiter(redis_client, settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client

    app.dependency_overrides.clear()


@pytest.fixture
async def merchant(session) -> Merchant:
    """El comercio principal de las pruebas."""
    return await session.get(Merchant, MERCHANT_ID)


@pytest.fixture
async def merchant_otro(session) -> Merchant:
    """Un segundo comercio, para comprobar el aislamiento."""
    return await session.get(Merchant, MERCHANT_ID_OTRO)


def ajustes_con(**overrides):
    """Construye unos Settings partiendo de los actuales.

    Los campos calculados no son parámetros del constructor, así que hay que
    excluirlos; tenerlo en un solo sitio evita que cada test repita la lista y
    se rompa al añadir uno nuevo.
    """
    from app.core.config import Settings, get_settings

    base = get_settings().model_dump(exclude={"admin_api_keys", "fraud_blocked_bins"})
    return Settings(**{**base, **overrides})
