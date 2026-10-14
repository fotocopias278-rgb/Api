"""comercios y claves de api

Revision ID: 0002_merchants
Revises: 0001_initial
Create Date: 2026-10-14 02:34:30.572370
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_merchants"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('merchants',
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('meta', sa.JSON(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_merchants'))
    )
    op.create_table('api_keys',
    sa.Column('merchant_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('prefix', sa.String(length=16), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('scopes', sa.JSON(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], name=op.f('fk_api_keys_merchant_id_merchants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_api_keys'))
    )
    op.create_index(op.f('ix_api_keys_key_hash'), 'api_keys', ['key_hash'], unique=True)
    op.create_index(op.f('ix_api_keys_merchant_id'), 'api_keys', ['merchant_id'], unique=False)
    op.add_column('payments', sa.Column('merchant_id', sa.Uuid(), nullable=True))
    op.drop_index(op.f('ix_payments_customer_created'), table_name='payments')
    op.drop_constraint(op.f('uq_payments_idempotency_key'), 'payments', type_='unique')
    op.create_index('ix_payments_merchant_customer_created', 'payments', ['merchant_id', 'customer_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_payments_merchant_id'), 'payments', ['merchant_id'], unique=False)
    op.create_unique_constraint('uq_payments_merchant_idempotency', 'payments', ['merchant_id', 'idempotency_key'])
    op.create_foreign_key(op.f('fk_payments_merchant_id_merchants'), 'payments', 'merchants', ['merchant_id'], ['id'], ondelete='CASCADE')
    op.add_column('receipts', sa.Column('merchant_id', sa.Uuid(), nullable=True))
    # --- Relleno de datos existentes -------------------------------------
    # Las filas anteriores a la multi-tenancia no tienen dueño. Se les asigna
    # un comercio heredado para no perderlas; quien actualice puede
    # renombrarlo o repartir los datos después.
    conexion = op.get_bind()
    pendientes = conexion.execute(
        sa.text("SELECT COUNT(*) FROM payments") 
    ).scalar_one()
    if pendientes:
        heredado = uuid.uuid4()
        conexion.execute(
            sa.text(
                "INSERT INTO merchants (id, name, is_active, meta, created_at, updated_at) "
                "VALUES (:id, :name, true, '{}', now(), now())"
            ),
            {"id": heredado, "name": "Comercio heredado"},
        )
        conexion.execute(
            sa.text("UPDATE payments SET merchant_id = :id WHERE merchant_id IS NULL"),
            {"id": heredado},
        )
        conexion.execute(
            sa.text("UPDATE receipts SET merchant_id = :id WHERE merchant_id IS NULL"),
            {"id": heredado},
        )

    # Ya con dueño, se endurecen las columnas.
    op.alter_column('payments', 'merchant_id', nullable=False)
    op.alter_column('receipts', 'merchant_id', nullable=False)
    op.create_index(op.f('ix_receipts_merchant_id'), 'receipts', ['merchant_id'], unique=False)
    op.create_foreign_key(op.f('fk_receipts_merchant_id_merchants'), 'receipts', 'merchants', ['merchant_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_receipts_merchant_id_merchants'), 'receipts', type_='foreignkey')
    op.drop_index(op.f('ix_receipts_merchant_id'), table_name='receipts')
    op.drop_column('receipts', 'merchant_id')
    op.drop_constraint(op.f('fk_payments_merchant_id_merchants'), 'payments', type_='foreignkey')
    op.drop_constraint('uq_payments_merchant_idempotency', 'payments', type_='unique')
    op.drop_index(op.f('ix_payments_merchant_id'), table_name='payments')
    op.drop_index('ix_payments_merchant_customer_created', table_name='payments')
    op.create_unique_constraint(op.f('uq_payments_idempotency_key'), 'payments', ['idempotency_key'], postgresql_nulls_not_distinct=False)
    op.create_index(op.f('ix_payments_customer_created'), 'payments', ['customer_id', 'created_at'], unique=False)
    op.drop_column('payments', 'merchant_id')
    op.drop_index(op.f('ix_api_keys_merchant_id'), table_name='api_keys')
    op.drop_index(op.f('ix_api_keys_key_hash'), table_name='api_keys')
    op.drop_table('api_keys')
    op.drop_table('merchants')
