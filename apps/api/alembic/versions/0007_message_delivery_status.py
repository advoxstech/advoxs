"""delivery_status em messages

Marca se uma mensagem de saída (agent/human) foi entregue ao WhatsApp com
sucesso — nullable porque só é significativo pra sender_type agent/human;
mensagens de contato e mensagens já existentes antes desta migration ficam
NULL, sem retroatividade.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-09
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("delivery_status", sa.String(), nullable=True))
    op.create_check_constraint(
        "delivery_status", "messages", "delivery_status IN ('sent', 'failed')"
    )


def downgrade() -> None:
    op.drop_constraint("delivery_status", "messages", type_="check")
    op.drop_column("messages", "delivery_status")
