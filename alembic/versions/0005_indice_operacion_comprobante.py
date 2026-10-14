"""indice operacion comprobante

Revision ID: 0005_indice_operacion
Revises: 0004_webhooks_salientes
Create Date: 2026-10-14 03:34:11.981041
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_indice_operacion"
down_revision: str | None = "0004_webhooks_salientes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('receipts', sa.Column('operation_number', sa.String(length=128), nullable=True))

    # Los comprobantes ya analizados llevan el número dentro del JSON de
    # extracción. Sin este relleno, la detección de justificantes reutilizados
    # sería ciega a todo el histórico tras cambiar a la consulta indexada.
    conexion = op.get_bind()
    if conexion.dialect.name == "postgresql":
        conexion.execute(
            sa.text(
                "UPDATE receipts SET operation_number = extraction->>'operation_number' "
                "WHERE extraction IS NOT NULL AND extraction->>'operation_number' IS NOT NULL"
            )
        )
    else:
        # SQLite usa json_extract en vez del operador ->> de PostgreSQL.
        conexion.execute(
            sa.text(
                "UPDATE receipts SET operation_number = "
                "json_extract(extraction, '$.operation_number') "
                "WHERE extraction IS NOT NULL "
                "AND json_extract(extraction, '$.operation_number') IS NOT NULL"
            )
        )

    op.create_index('ix_receipts_merchant_operation', 'receipts', ['merchant_id', 'operation_number'], unique=False)
    op.create_index(op.f('ix_receipts_operation_number'), 'receipts', ['operation_number'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_receipts_operation_number'), table_name='receipts')
    op.drop_index('ix_receipts_merchant_operation', table_name='receipts')
    op.drop_column('receipts', 'operation_number')
