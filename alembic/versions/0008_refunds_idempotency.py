"""refunds idempotentes e acumulado de devoluciones

Revision ID: 0008_refunds_idempotency
Revises: 0007_trabajos_conciliacion
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_refunds_idempotency"
down_revision: str | None = "0007_trabajos_conciliacion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column(
            "refunded_amount_minor",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "payments",
        sa.Column("idempotency_payload_hash", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_payments_refund_amount_in_range"),
        "payments",
        "refunded_amount_minor >= 0 AND refunded_amount_minor <= amount_minor",
    )
    op.create_table(
        "refund_operations",
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("requested_amount_minor", sa.Integer(), nullable=True),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider_refund_id", sa.String(length=128), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount_minor > 0", name=op.f("ck_refund_operations_amount_positive")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name=op.f("ck_refund_operations_status_valid"),
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_refund_operations"),
        sa.UniqueConstraint(
            "merchant_id", "idempotency_key", name="uq_refund_merchant_idempotency"
        ),
    )
    op.create_index(
        "ix_refund_operations_merchant_id", "refund_operations", ["merchant_id"]
    )
    op.create_index("ix_refund_operations_payment_id", "refund_operations", ["payment_id"])
    op.create_index(
        "ix_refunds_payment_created",
        "refund_operations",
        ["payment_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_refunds_payment_created", table_name="refund_operations")
    op.drop_index("ix_refund_operations_payment_id", table_name="refund_operations")
    op.drop_index("ix_refund_operations_merchant_id", table_name="refund_operations")
    op.drop_table("refund_operations")
    op.drop_constraint(
        "ck_payments_refund_amount_in_range", "payments", type_="check"
    )
    op.drop_column("payments", "idempotency_payload_hash")
    op.drop_column("payments", "refunded_amount_minor")
