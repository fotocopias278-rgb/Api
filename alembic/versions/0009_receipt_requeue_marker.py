"""marca de reencolado de comprobantes

Revision ID: 0009_receipt_requeue_marker
Revises: 0008_refunds_idempotency
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_receipt_requeue_marker"
down_revision: str | None = "0008_refunds_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "receipts",
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_receipts_requeue",
        "receipts",
        ["status", "last_enqueued_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_receipts_requeue", table_name="receipts")
    op.drop_column("receipts", "last_enqueued_at")
