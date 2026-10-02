"""sinalização de conversas urgentes + palavras-chave por escritório

A lista padrão é duplicada de app/services/urgency.py de propósito (migration
congelada, mesmo princípio da 0015) — mudar a lista viva não altera o
backfill já aplicado.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-02
"""

import unicodedata

import sqlalchemy as sa

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

_DEFAULT_KEYWORDS = [
    "preso",
    "presa",
    "prisão",
    "flagrante",
    "delegacia",
    "medida protetiva",
    "ameaça",
    "violência",
    "agressão",
    "audiência amanhã",
    "audiência hoje",
    "prazo vence hoje",
    "prazo vence amanhã",
    "intimação",
    "liminar",
    "despejo",
    "penhora",
    "bloqueio de conta",
    "leilão",
    "busca e apreensão",
    "mandado",
]


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_accents.split())


def upgrade() -> None:
    op.add_column("conversations", sa.Column("urgent_since", sa.DateTime(timezone=True)))
    op.add_column("conversations", sa.Column("urgent_reason", sa.Text()))
    op.add_column("conversations", sa.Column("urgent_source", sa.String()))
    op.create_check_constraint(
        "urgent_source",
        "conversations",
        "urgent_source IS NULL OR urgent_source IN ('agent', 'keyword', 'manual')",
    )
    op.create_index(
        "ix_conversations_tenant_urgent",
        "conversations",
        ["tenant_id", "urgent_since"],
        postgresql_where=sa.text("urgent_since IS NOT NULL"),
    )

    op.create_table(
        "urgency_keywords",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("keyword", sa.String(), nullable=False),
        sa.Column("normalized", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "normalized"),
    )
    op.create_index("ix_urgency_keywords_tenant_id", "urgency_keywords", ["tenant_id"])
    op.execute("ALTER TABLE urgency_keywords ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON urgency_keywords "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )

    bind = op.get_bind()
    for keyword in _DEFAULT_KEYWORDS:
        bind.execute(
            sa.text(
                "INSERT INTO urgency_keywords (tenant_id, keyword, normalized) "
                "SELECT id, :keyword, :normalized FROM tenants "
                "ON CONFLICT (tenant_id, normalized) DO NOTHING"
            ),
            {"keyword": keyword, "normalized": _normalize(keyword)},
        )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON urgency_keywords")
    op.drop_index("ix_urgency_keywords_tenant_id", table_name="urgency_keywords")
    op.drop_table("urgency_keywords")
    op.drop_index("ix_conversations_tenant_urgent", table_name="conversations")
    op.drop_constraint("urgent_source", "conversations", type_="check")
    op.drop_column("conversations", "urgent_source")
    op.drop_column("conversations", "urgent_reason")
    op.drop_column("conversations", "urgent_since")
