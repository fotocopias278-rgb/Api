"""aísla eventos entrantes y completa el acumulado de devoluciones

Revision ID: 0011_privacy_backfill
Revises: 0010_webhook_outbox_marker
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_privacy_backfill"
down_revision: str | None = "0010_webhook_outbox_marker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "webhook_events",
        sa.Column(
            "merchant_id",
            sa.Uuid(),
            sa.ForeignKey(
                "merchants.id",
                name="fk_webhook_events_merchant_id_merchants",
                ondelete="CASCADE",
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_webhook_events_merchant_created",
        "webhook_events",
        ["merchant_id", "created_at"],
    )
    op.add_column(
        "receipts",
        sa.Column("requeue_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.execute(
        sa.text(
            "UPDATE payments SET refunded_amount_minor = amount_minor "
            "WHERE status = 'refunded' AND refunded_amount_minor = 0"
        )
    )


def downgrade() -> None:
    op.drop_column("receipts", "requeue_attempts")
    op.drop_index("ix_webhook_events_merchant_created", table_name="webhook_events")
    op.drop_column("webhook_events", "merchant_id")
