"""summary e summary_generated_at em conversations

Resumo de conversa sob demanda, gerado via LLM (agents service) e
persistido aqui — sem histórico, cada geração sobrescreve a anterior.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-09
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("summary_generated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "summary_generated_at")
    op.drop_column("conversations", "summary")
