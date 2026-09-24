"""Agent drafts, immutable publication history and isolated test snapshots.

Revision ID: 0038
Revises: 0037
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("draft_name", sa.String(), nullable=True))
    op.add_column("agents", sa.Column("draft_instructions", sa.Text(), nullable=True))
    op.add_column(
        "agents", sa.Column("draft_revision", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "agents", sa.Column("published_version", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column("agents", sa.Column("restored_from_version", sa.Integer(), nullable=True))
    op.create_table(
        "agent_versions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("restored_from_version", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("agent_id", "number"),
    )
    op.create_table(
        "agent_test_sessions",
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("agents_snapshot", postgresql.JSONB(), nullable=False),
    )
    # Copy existing instructions verbatim. Neither live configuration nor customer memory changes.
    op.execute("""
        INSERT INTO agent_versions (tenant_id, agent_id, number, name, instructions, description)
        SELECT tenant_id, id, 1, name, instructions, 'Configuração inicial importada'
        FROM agents
    """)
    # Covers every creation path, including self-service provisioning and SQL seed scripts.
    op.execute("""
        CREATE FUNCTION initialize_agent_version() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO agent_versions
                (tenant_id, agent_id, number, name, instructions, description)
            VALUES (NEW.tenant_id, NEW.id, 1, NEW.name, NEW.instructions, 'Publicação inicial');
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER agent_initial_version AFTER INSERT ON agents
        FOR EACH ROW EXECUTE FUNCTION initialize_agent_version()
    """)
    for table in ("agent_versions", "agent_test_sessions"):
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER agent_initial_version ON agents")
    op.execute("DROP FUNCTION initialize_agent_version()")
    op.drop_table("agent_test_sessions")
    op.drop_table("agent_versions")
    for column in (
        "restored_from_version",
        "published_version",
        "draft_revision",
        "draft_instructions",
        "draft_name",
    ):
        op.drop_column("agents", column)
