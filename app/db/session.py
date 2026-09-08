"""Motor y sesiones asíncronas de SQLAlchemy."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings, settings

logger = logging.getLogger(__name__)


def usa_pooler_de_transacciones(url: str) -> bool:
    """Detecta un endpoint agrupado en modo transacción (PgBouncer).

    Neon los nombra con el sufijo `-pooler`. Importa porque asyncpg usa
    sentencias preparadas por defecto y PgBouncer en modo transacción las
    rompe: el error aparece de forma intermitente y bajo carga, que es la
    peor forma de descubrirlo.
    """
    return "-pooler." in url


def engine_kwargs(config: Settings = settings) -> dict[str, object]:
    """Calcula los parámetros del motor según el destino.

    Se aísla como función pura para poder comprobarla: los `connect_args` no
    son inspeccionables una vez construido el motor.
    """
    kwargs: dict[str, object] = {"echo": config.db_echo, "pool_pre_ping": True}

    # SQLite (tests) usa un pool sin dimensionar: pasarle `pool_size` falla.
    if not config.database_url.startswith("sqlite"):
        kwargs["pool_size"] = config.db_pool_size
        kwargs["max_overflow"] = config.db_max_overflow

    if "asyncpg" in config.database_url and usa_pooler_de_transacciones(config.database_url):
        # Configuración documentada por SQLAlchemy para PgBouncer:
        #  - sin caché de sentencias preparadas, que queda obsoleta en cuanto la
        #    conexión se reutiliza para otra sesión;
        #  - nombres únicos, porque la numeración correlativa de asyncpg colisiona
        #    entre conexiones agrupadas;
        #  - sin pool en el cliente: ya hay uno en el servidor.
        # El coste es un handshake TLS por conexión. Si la latencia molesta, usa
        # el endpoint directo (el mismo host sin `-pooler`).
        kwargs["connect_args"] = {
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
        }
        kwargs["poolclass"] = NullPool
        kwargs.pop("pool_size", None)  # NullPool no admite dimensionado
        kwargs.pop("max_overflow", None)

    return kwargs


def build_engine(config: Settings = settings) -> AsyncEngine:
    """Crea el motor asíncrono."""
    kwargs = engine_kwargs(config)
    if kwargs.get("poolclass") is NullPool:
        logger.info("db.pooler_de_transacciones", extra={"sentencias_preparadas": False})
    return create_async_engine(config.database_url, **kwargs)


engine: AsyncEngine = build_engine()

SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Sesión transaccional para código fuera del ciclo de petición (workers)."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
