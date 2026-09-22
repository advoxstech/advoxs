"""separa a entrega das respostas geradas pela IA

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-22
"""

import sqlalchemy as sa

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("delivery_status", "messages", type_="check")
    op.create_check_constraint(
        "delivery_status",
        "messages",
        "delivery_status IN ('pending', 'sent', 'failed')",
    )
    op.create_table(
        "outbound_message_jobs",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'delivered', 'failed')",
            name="outbound_message_jobs_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index(
        "ix_outbound_message_jobs_recovery",
        "outbound_message_jobs",
        ["status", "available_at", "last_enqueued_at"],
    )
    op.execute("ALTER TABLE outbound_message_jobs ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON outbound_message_jobs "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON outbound_message_jobs")
    op.execute("ALTER TABLE outbound_message_jobs DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_outbound_message_jobs_recovery", table_name="outbound_message_jobs")
    op.drop_table("outbound_message_jobs")
    op.drop_constraint("delivery_status", "messages", type_="check")
    op.create_check_constraint(
        "delivery_status", "messages", "delivery_status IN ('sent', 'failed')"
    )
