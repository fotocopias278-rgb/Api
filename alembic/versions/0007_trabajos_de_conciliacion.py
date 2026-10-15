"""trabajos de conciliacion

Revision ID: 0007_trabajos_conciliacion
Revises: 0006_webhook_por_proveedor
Create Date: 2026-10-15 03:07:37.407570
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_trabajos_conciliacion"
down_revision: str | None = "0006_webhook_por_proveedor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('reconciliation_jobs',
    sa.Column('merchant_id', sa.Uuid(), nullable=False),
    sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', name='jobstatus', native_enum=False, length=32), nullable=False),
    sa.Column('total_payments', sa.Integer(), nullable=False),
    sa.Column('matched_count', sa.Integer(), nullable=False),
    sa.Column('mismatched_count', sa.Integer(), nullable=False),
    sa.Column('pending_count', sa.Integer(), nullable=False),
    sa.Column('duplicate_count', sa.Integer(), nullable=False),
    sa.Column('unmatched_receipts_count', sa.Integer(), nullable=False),
    sa.Column('discrepancies', sa.JSON(), nullable=False),
    sa.Column('error_message', sa.String(length=512), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], name=op.f('fk_reconciliation_jobs_merchant_id_merchants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reconciliation_jobs'))
    )
    op.create_index('ix_jobs_merchant_created', 'reconciliation_jobs', ['merchant_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_reconciliation_jobs_merchant_id'), 'reconciliation_jobs', ['merchant_id'], unique=False)
    op.create_index(op.f('ix_reconciliation_jobs_status'), 'reconciliation_jobs', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_reconciliation_jobs_status'), table_name='reconciliation_jobs')
    op.drop_index(op.f('ix_reconciliation_jobs_merchant_id'), table_name='reconciliation_jobs')
    op.drop_index('ix_jobs_merchant_created', table_name='reconciliation_jobs')
    op.drop_table('reconciliation_jobs')
