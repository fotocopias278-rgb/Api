"""Motor y sesiones asíncronas de SQLAlchemy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, settings


def build_engine(config: Settings = settings) -> AsyncEngine:
    """Crea el motor asíncrono con parámetros dependientes del driver."""
    kwargs: dict[str, object] = {"echo": config.db_echo, "pool_pre_ping": True}
    # SQLite (tests) usa un pool sin dimensionar: pasarle `pool_size` falla.
    if not config.database_url.startswith("sqlite"):
        kwargs["pool_size"] = config.db_pool_size
        kwargs["max_overflow"] = config.db_max_overflow
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
