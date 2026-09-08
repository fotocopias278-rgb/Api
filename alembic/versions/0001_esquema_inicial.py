"""esquema inicial

Revision ID: 0001_initial
Revises: 
Create Date: 2026-09-08 01:04:52.359849
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('payments',
    sa.Column('reference', sa.String(length=64), nullable=False),
    sa.Column('customer_id', sa.String(length=64), nullable=False),
    sa.Column('amount_minor', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'UNDER_REVIEW', 'PROCESSING', 'SUCCEEDED', 'FAILED', 'REJECTED', 'REFUNDED', name='paymentstatus', native_enum=False, length=32), nullable=False),
    sa.Column('method', sa.Enum('CARD', 'BANK_TRANSFER', name='paymentmethod', native_enum=False, length=32), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('provider_payment_id', sa.String(length=128), nullable=True),
    sa.Column('idempotency_key', sa.String(length=128), nullable=True),
    sa.Column('risk_score', sa.Integer(), nullable=True),
    sa.Column('risk_decision', sa.Enum('APPROVE', 'REVIEW', 'REJECT', name='riskdecision', native_enum=False, length=32), nullable=True),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('card_bin', sa.String(length=8), nullable=True),
    sa.Column('card_country', sa.String(length=2), nullable=True),
    sa.Column('ip_country', sa.String(length=2), nullable=True),
    sa.Column('failure_reason', sa.String(length=255), nullable=True),
    sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('meta', sa.JSON(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('amount_minor > 0', name=op.f('ck_payments_amount_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payments')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_payments_idempotency_key')),
    sa.UniqueConstraint('provider_payment_id', name=op.f('uq_payments_provider_payment_id'))
    )
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.create_index('ix_payments_customer_created', ['customer_id', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_payments_customer_id'), ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_payments_reference'), ['reference'], unique=True)
        batch_op.create_index(batch_op.f('ix_payments_risk_decision'), ['risk_decision'], unique=False)
        batch_op.create_index(batch_op.f('ix_payments_status'), ['status'], unique=False)

    op.create_table('webhook_events',
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('event_id', sa.String(length=128), nullable=False),
    sa.Column('event_type', sa.String(length=128), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.String(length=512), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_webhook_events'))
    )
    with op.batch_alter_table('webhook_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_webhook_events_event_id'), ['event_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_webhook_events_event_type'), ['event_type'], unique=False)

    op.create_table('fraud_assessments',
    sa.Column('payment_id', sa.Uuid(), nullable=False),
    sa.Column('score', sa.Integer(), nullable=False),
    sa.Column('decision', sa.Enum('APPROVE', 'REVIEW', 'REJECT', name='riskdecision', native_enum=False, length=32), nullable=False),
    sa.Column('engine_version', sa.String(length=32), nullable=False),
    sa.Column('signals', sa.JSON(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['payment_id'], ['payments.id'], name=op.f('fk_fraud_assessments_payment_id_payments'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fraud_assessments'))
    )
    with op.batch_alter_table('fraud_assessments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fraud_assessments_payment_id'), ['payment_id'], unique=False)

    op.create_table('receipts',
    sa.Column('payment_id', sa.Uuid(), nullable=True),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('content_sha256', sa.String(length=64), nullable=False),
    sa.Column('storage_uri', sa.String(length=512), nullable=True),
    sa.Column('status', sa.Enum('PENDING', 'PROCESSING', 'EXTRACTED', 'FAILED', name='receiptstatus', native_enum=False, length=32), nullable=False),
    sa.Column('reconciliation_status', sa.Enum('PENDING', 'MATCHED', 'MISMATCHED', 'DUPLICATE', 'UNREADABLE', name='reconciliationstatus', native_enum=False, length=32), nullable=False),
    sa.Column('extraction', sa.JSON(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('reconciliation_notes', sa.JSON(), nullable=False),
    sa.Column('error_message', sa.String(length=512), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['payment_id'], ['payments.id'], name=op.f('fk_receipts_payment_id_payments'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_receipts'))
    )
    with op.batch_alter_table('receipts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_receipts_content_sha256'), ['content_sha256'], unique=False)
        batch_op.create_index(batch_op.f('ix_receipts_payment_id'), ['payment_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_receipts_reconciliation_status'), ['reconciliation_status'], unique=False)
        batch_op.create_index(batch_op.f('ix_receipts_status'), ['status'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('receipts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_receipts_status'))
        batch_op.drop_index(batch_op.f('ix_receipts_reconciliation_status'))
        batch_op.drop_index(batch_op.f('ix_receipts_payment_id'))
        batch_op.drop_index(batch_op.f('ix_receipts_content_sha256'))

    op.drop_table('receipts')
    with op.batch_alter_table('fraud_assessments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fraud_assessments_payment_id'))

    op.drop_table('fraud_assessments')
    with op.batch_alter_table('webhook_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_webhook_events_event_type'))
        batch_op.drop_index(batch_op.f('ix_webhook_events_event_id'))

    op.drop_table('webhook_events')
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payments_status'))
        batch_op.drop_index(batch_op.f('ix_payments_risk_decision'))
        batch_op.drop_index(batch_op.f('ix_payments_reference'))
        batch_op.drop_index(batch_op.f('ix_payments_customer_id'))
        batch_op.drop_index('ix_payments_customer_created')

    op.drop_table('payments')
