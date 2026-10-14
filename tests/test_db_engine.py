"""Configuración del motor según el destino."""

from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.db.session import build_engine, engine_kwargs, usa_pooler_de_transacciones

POOLER = ("postgresql+asyncpg://u:c@ep-blue-mode-ae56za91-pooler.c-2.us-east-2."
          "aws.neon.tech/neondb?ssl=require")
DIRECTO = ("postgresql+asyncpg://u:c@ep-blue-mode-ae56za91.c-2.us-east-2."
           "aws.neon.tech/neondb?ssl=require")


def ajustes(url: str) -> Settings:
    base = get_settings().model_dump(exclude={"admin_api_keys", "fraud_blocked_bins"})
    return Settings(**{**base, "database_url": url})


def test_se_detecta_el_endpoint_agrupado():
    assert usa_pooler_de_transacciones(POOLER)
    assert not usa_pooler_de_transacciones(DIRECTO)
    assert not usa_pooler_de_transacciones("sqlite+aiosqlite:///./t.db")


def test_tras_un_pooler_no_se_cachean_sentencias_preparadas():
    """PgBouncer en modo transacción las rompe de forma intermitente."""
    kwargs = engine_kwargs(ajustes(POOLER))
    conectar = kwargs["connect_args"]

    assert conectar["prepared_statement_cache_size"] == 0
    # Nombres únicos: la numeración correlativa de asyncpg colisiona al agrupar.
    nombre = conectar["prepared_statement_name_func"]()
    assert nombre.startswith("__asyncpg_") and nombre != conectar["prepared_statement_name_func"]()


def test_tras_un_pooler_no_se_agrupa_tambien_en_el_cliente():
    kwargs = engine_kwargs(ajustes(POOLER))
    assert kwargs["poolclass"] is NullPool
    # NullPool no admite dimensionado: pasarlo revienta al construir el motor.
    assert "pool_size" not in kwargs
    assert "max_overflow" not in kwargs
    build_engine(ajustes(POOLER))  # no debe lanzar


def test_un_endpoint_directo_usa_el_pool_normal():
    kwargs = engine_kwargs(ajustes(DIRECTO))
    assert "connect_args" not in kwargs
    assert "poolclass" not in kwargs
    assert kwargs["pool_size"] == get_settings().db_pool_size


def test_sqlite_no_recibe_parametros_de_pool():
    kwargs = engine_kwargs(ajustes("sqlite+aiosqlite:///./tmp-test.db"))
    assert "pool_size" not in kwargs
