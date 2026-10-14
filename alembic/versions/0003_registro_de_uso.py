"""registro de uso

Revision ID: 0003_uso
Revises: 0002_merchants
Create Date: 2026-10-14 02:43:41.175347
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_uso"
down_revision: str | None = "0002_merchants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('usage_events',
    sa.Column('merchant_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=48), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('meta', sa.JSON(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], name=op.f('fk_usage_events_merchant_id_merchants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_usage_events'))
    )
    op.create_index(op.f('ix_usage_events_kind'), 'usage_events', ['kind'], unique=False)
    op.create_index(op.f('ix_usage_events_merchant_id'), 'usage_events', ['merchant_id'], unique=False)
    op.create_index('ix_usage_merchant_created', 'usage_events', ['merchant_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_usage_merchant_created', table_name='usage_events')
    op.drop_index(op.f('ix_usage_events_merchant_id'), table_name='usage_events')
    op.drop_index(op.f('ix_usage_events_kind'), table_name='usage_events')
    op.drop_table('usage_events')
