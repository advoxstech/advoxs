"""is_test em conversations

Conversas de teste (aba Testes do painel): o tenant conversa com os próprios
agentes sem WhatsApp, com contato sintético teste-{hex12}. Default false —
nenhuma conversa existente vira teste.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-16
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("conversations", "is_test")
