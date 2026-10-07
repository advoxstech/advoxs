"""Palavras-chave de urgência por agente.

Revision ID: 0044
Revises: 0043
"""

import sqlalchemy as sa

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("urgency_keywords", sa.Column("agent_id", sa.Uuid(), nullable=True))
    # A constraint global antiga precisa sair antes da replicação: a mesma
    # palavra passará a existir uma vez para cada agente do tenant.
    op.drop_constraint(
        "uq_urgency_keywords_tenant_id",
        "urgency_keywords",
        type_="unique",
    )

    # Replica a configuração vigente para todos os agentes. Assim o deploy não
    # muda quais mensagens são detectadas até o escritório personalizar as listas.
    op.execute(
        """
        INSERT INTO urgency_keywords (id, tenant_id, agent_id, keyword, normalized, created_at)
        SELECT gen_random_uuid(), uk.tenant_id, a.id, uk.keyword, uk.normalized, uk.created_at
        FROM urgency_keywords AS uk
        JOIN agents AS a ON a.tenant_id = uk.tenant_id
        WHERE uk.agent_id IS NULL
        """
    )
    op.execute("DELETE FROM urgency_keywords WHERE agent_id IS NULL")

    op.alter_column("urgency_keywords", "agent_id", nullable=False)
    op.create_foreign_key(
        "fk_urgency_keywords_agent_id",
        "urgency_keywords",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_urgency_keywords_tenant_agent_normalized",
        "urgency_keywords",
        ["tenant_id", "agent_id", "normalized"],
    )
    op.create_index(
        "ix_urgency_keywords_tenant_agent",
        "urgency_keywords",
        ["tenant_id", "agent_id"],
    )

    op.add_column("conversations", sa.Column("urgent_agent_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_conversations_urgent_agent_id",
        "conversations",
        "agents",
        ["urgent_agent_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_conversations_urgent_agent_id", "conversations", type_="foreignkey")
    op.drop_column("conversations", "urgent_agent_id")

    # Uma palavra pode existir em vários agentes. Mantém uma cópia por tenant
    # para restaurar o formato global anterior sem violar a constraint antiga.
    op.execute(
        """
        DELETE FROM urgency_keywords AS uk
        USING urgency_keywords AS keep
        WHERE uk.tenant_id = keep.tenant_id
          AND uk.normalized = keep.normalized
          AND uk.id > keep.id
        """
    )
    op.drop_index("ix_urgency_keywords_tenant_agent", table_name="urgency_keywords")
    op.drop_constraint(
        "uq_urgency_keywords_tenant_agent_normalized",
        "urgency_keywords",
        type_="unique",
    )
    op.drop_constraint("fk_urgency_keywords_agent_id", "urgency_keywords", type_="foreignkey")
    op.drop_column("urgency_keywords", "agent_id")
    op.create_unique_constraint(
        "uq_urgency_keywords_tenant_id",
        "urgency_keywords",
        ["tenant_id", "normalized"],
    )
