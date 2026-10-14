"""webhooks salientes

Revision ID: 0004_webhooks_salientes
Revises: 0003_uso
Create Date: 2026-10-14 02:50:16.976832
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_webhooks_salientes"
down_revision: str | None = "0003_uso"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('webhook_endpoints',
    sa.Column('merchant_id', sa.Uuid(), nullable=False),
    sa.Column('url', sa.String(length=512), nullable=False),
    sa.Column('secret', sa.String(length=128), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('event_types', sa.JSON(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], name=op.f('fk_webhook_endpoints_merchant_id_merchants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_webhook_endpoints'))
    )
    op.create_index(op.f('ix_webhook_endpoints_merchant_id'), 'webhook_endpoints', ['merchant_id'], unique=False)
    op.create_table('webhook_deliveries',
    sa.Column('endpoint_id', sa.Uuid(), nullable=False),
    sa.Column('merchant_id', sa.Uuid(), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=True),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.String(length=512), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['endpoint_id'], ['webhook_endpoints.id'], name=op.f('fk_webhook_deliveries_endpoint_id_webhook_endpoints'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], name=op.f('fk_webhook_deliveries_merchant_id_merchants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_webhook_deliveries'))
    )
    op.create_index('ix_deliveries_merchant_created', 'webhook_deliveries', ['merchant_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_webhook_deliveries_endpoint_id'), 'webhook_deliveries', ['endpoint_id'], unique=False)
    op.create_index(op.f('ix_webhook_deliveries_event_type'), 'webhook_deliveries', ['event_type'], unique=False)
    op.create_index(op.f('ix_webhook_deliveries_merchant_id'), 'webhook_deliveries', ['merchant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_webhook_deliveries_merchant_id'), table_name='webhook_deliveries')
    op.drop_index(op.f('ix_webhook_deliveries_event_type'), table_name='webhook_deliveries')
    op.drop_index(op.f('ix_webhook_deliveries_endpoint_id'), table_name='webhook_deliveries')
    op.drop_index('ix_deliveries_merchant_created', table_name='webhook_deliveries')
    op.drop_table('webhook_deliveries')
    op.drop_index(op.f('ix_webhook_endpoints_merchant_id'), table_name='webhook_endpoints')
    op.drop_table('webhook_endpoints')
