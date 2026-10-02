"""adiciona exclusão durável de conversas

Revision ID: 0040
Revises: 0039
Create Date: 2026-09-26
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.drop_constraint(
        op.f("fk_usage_records_conversation_id_conversations"),
        "usage_records",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_usage_records_related_message_id_messages"),
        "usage_records",
        type_="foreignkey",
    )
    op.alter_column("usage_records", "conversation_id", nullable=True)
    op.alter_column("usage_records", "related_message_id", nullable=True)
    op.create_foreign_key(
        op.f("fk_usage_records_conversation_id_conversations"),
        "usage_records",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_usage_records_related_message_id_messages"),
        "usage_records",
        "messages",
        ["related_message_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "conversation_cleanup_jobs",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "message_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "attachment_document_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True)),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing')",
            name="conversation_cleanup_jobs_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id"),
    )
    op.create_index(
        "ix_conversation_cleanup_jobs_recovery",
        "conversation_cleanup_jobs",
        ["status", "available_at", "last_enqueued_at"],
    )
    op.execute("ALTER TABLE conversation_cleanup_jobs ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON conversation_cleanup_jobs "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON conversation_cleanup_jobs")
    op.execute("ALTER TABLE conversation_cleanup_jobs DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_conversation_cleanup_jobs_recovery", table_name="conversation_cleanup_jobs")
    op.drop_table("conversation_cleanup_jobs")

    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM usage_records "
        "WHERE conversation_id IS NULL OR related_message_id IS NULL) THEN "
        "RAISE EXCEPTION 'downgrade recusado: usage_records preservados sem conversa/mensagem'; "
        "END IF; END $$"
    )
    op.drop_constraint(
        op.f("fk_usage_records_related_message_id_messages"),
        "usage_records",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_usage_records_conversation_id_conversations"),
        "usage_records",
        type_="foreignkey",
    )
    op.alter_column("usage_records", "related_message_id", nullable=False)
    op.alter_column("usage_records", "conversation_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_usage_records_related_message_id_messages"),
        "usage_records",
        "messages",
        ["related_message_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_usage_records_conversation_id_conversations"),
        "usage_records",
        "conversations",
        ["conversation_id"],
        ["id"],
    )
    op.drop_column("conversations", "deletion_requested_at")
