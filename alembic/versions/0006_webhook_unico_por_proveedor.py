"""unicidad de webhook por proveedor

Revision ID: 0006_webhook_por_proveedor
Revises: 0005_indice_operacion
Create Date: 2026-10-14 03:39:54.707872
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_webhook_por_proveedor"
down_revision: str | None = "0005_indice_operacion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(op.f('ix_webhook_events_event_id'), table_name='webhook_events')
    op.create_index(op.f('ix_webhook_events_event_id'), 'webhook_events', ['event_id'], unique=False)
    op.create_unique_constraint('uq_webhook_events_provider_event', 'webhook_events', ['provider', 'event_id'])


def downgrade() -> None:
    op.drop_constraint('uq_webhook_events_provider_event', 'webhook_events', type_='unique')
    op.drop_index(op.f('ix_webhook_events_event_id'), table_name='webhook_events')
    op.create_index(op.f('ix_webhook_events_event_id'), 'webhook_events', ['event_id'], unique=True)
