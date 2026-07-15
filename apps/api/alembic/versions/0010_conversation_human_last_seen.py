"""human_last_seen_at em conversations

Presença do atendente no takeover: atualizado pelo heartbeat do painel e
pelo PATCH pra human; o worker compara com HUMAN_TAKEOVER_TIMEOUT_SECONDS
pra reverter a conversa pra agent quando o atendente some (reversão lazy,
na chegada da próxima mensagem do contato).

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-15
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("human_last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "human_last_seen_at")
