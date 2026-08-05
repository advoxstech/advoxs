"""conversations.current_agent_id — qual agente está respondendo agora

Guarda o agente do tenant que respondeu por último numa conversa (real ou de
teste), pra exibir "{nome do agente} respondendo" no painel em vez do texto
genérico "agente respondendo" — ver apps/worker/app/tasks/messages.py
(process_inbound_message) e a rota de test-messages em apps/api, que passam
a persistir esse valor a cada resposta do agente. SET NULL na exclusão do
agente: uma conversa "presa" num agente apagado só cai de volta pro texto
genérico, nunca impede a exclusão.

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-31
"""

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("current_agent_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversations_current_agent_id",
        "conversations",
        "agents",
        ["current_agent_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_conversations_current_agent_id", "conversations", type_="foreignkey")
    op.drop_column("conversations", "current_agent_id")
