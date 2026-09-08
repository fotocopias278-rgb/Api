"""Configuración común de los tests.

Las variables de entorno se fijan **antes** de importar nada de `app`: la
configuración es un singleton que se construye al importarse el módulo.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator

_TMP_DIR = tempfile.mkdtemp(prefix="finai-tests-")
_DB_PATH = os.path.join(_TMP_DIR, "test.db")

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite+aiosqlite:///{_DB_PATH}",
        "API_KEYS": "test-key-primaria,test-key-secundaria",
        "RECEIPT_STORAGE_DIR": os.path.join(_TMP_DIR, "receipts"),
        "STRIPE_WEBHOOK_SECRET": "whsec_test_secret",
        "FRAUD_BLOCKED_BINS": "666666",
        "LOG_LEVEL": "WARNING",
    }
)

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.deps import get_session  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db import models  # noqa: E402,F401 - registra las tablas
from app.db.base import Base  # noqa: E402
from app.main import create_app  # noqa: E402

API_KEY = "test-key-primaria"
AUTH_HEADERS = {"X-API-Key": API_KEY}


@pytest.fixture
async def engine():
    """Motor SQLite con el esquema creado desde el metadata."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{_DB_PATH}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client

    app.dependency_overrides.clear()
