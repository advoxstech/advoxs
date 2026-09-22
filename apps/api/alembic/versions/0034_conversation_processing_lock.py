"""serializa processamento de mensagens por conversa

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-22
"""

import sqlalchemy as sa

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_processing_locks",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["inbound_message_jobs.id"]),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.execute("ALTER TABLE conversation_processing_locks ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON conversation_processing_locks "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON conversation_processing_locks")
    op.execute("ALTER TABLE conversation_processing_locks DISABLE ROW LEVEL SECURITY")
    op.drop_table("conversation_processing_locks")
