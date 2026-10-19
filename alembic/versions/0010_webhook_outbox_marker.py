"""marca de reintento del outbox saliente

Revision ID: 0010_webhook_outbox_marker
Revises: 0009_receipt_requeue_marker
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_webhook_outbox_marker"
down_revision: str | None = "0009_receipt_requeue_marker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "webhook_deliveries",
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_deliveries_requeue",
        "webhook_deliveries",
        ["delivered_at", "last_enqueued_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_deliveries_requeue", table_name="webhook_deliveries")
    op.drop_column("webhook_deliveries", "last_enqueued_at")
