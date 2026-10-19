"""Evidencia PostgreSQL del backfill de devoluciones de la migración 0011."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import IS_POSTGRES, TEST_DATABASE_URL


@pytest.mark.skipif(not IS_POSTGRES, reason="requiere PostgreSQL real")
async def test_migracion_0011_rellena_refunds_anteriores_de_forma_idempotente():
    revision_file = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0011_reconciliation_privacy_backfill.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0011", revision_file)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    schema = f"test_migration_0011_{uuid.uuid4().hex}"
    payment_id = uuid.uuid4()
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await connection.execute(text("CREATE TABLE merchants (id UUID PRIMARY KEY)"))
            await connection.execute(
                text(
                    "CREATE TABLE payments (id UUID PRIMARY KEY, amount_minor INTEGER "
                    "NOT NULL, refunded_amount_minor INTEGER NOT NULL DEFAULT 0, "
                    "status VARCHAR(32) NOT NULL)"
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE webhook_events (id UUID PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL)"
                )
            )
            await connection.execute(text("CREATE TABLE receipts (id UUID PRIMARY KEY)"))
            await connection.execute(
                text(
                    "INSERT INTO payments (id, amount_minor, refunded_amount_minor, status) "
                    "VALUES (:id, 2500, 0, 'refunded')"
                ),
                {"id": payment_id},
            )

            def apply_and_repeat_backfill(sync_connection):
                context = MigrationContext.configure(sync_connection)
                with Operations.context(context):
                    migration.upgrade()
                return sync_connection.execute(
                    text(
                        "UPDATE payments SET refunded_amount_minor = amount_minor "
                        "WHERE status = 'refunded' AND refunded_amount_minor = 0"
                    )
                ).rowcount

            repeated_updates = await connection.run_sync(apply_and_repeat_backfill)
            refunded_amount = await connection.scalar(
                text("SELECT refunded_amount_minor FROM payments WHERE id = :id"),
                {"id": payment_id},
            )
            assert refunded_amount == 2_500
            assert repeated_updates == 0
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        await engine.dispose()
