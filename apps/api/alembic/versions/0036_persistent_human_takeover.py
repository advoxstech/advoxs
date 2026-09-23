"""torna o takeover humano persistente

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-23
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("delivery_status", "messages", type_="check")
    op.create_check_constraint(
        "delivery_status",
        "messages",
        "delivery_status IN ('pending', 'sent', 'failed', 'cancelled')",
    )
    op.drop_constraint("outbound_message_jobs_status", "outbound_message_jobs", type_="check")
    op.create_check_constraint(
        "outbound_message_jobs_status",
        "outbound_message_jobs",
        "status IN ('pending', 'processing', 'delivered', 'failed', 'cancelled')",
    )


def downgrade() -> None:
    op.execute("UPDATE messages SET delivery_status = 'failed' WHERE delivery_status = 'cancelled'")
    op.execute("UPDATE outbound_message_jobs SET status = 'failed' WHERE status = 'cancelled'")
    op.drop_constraint("outbound_message_jobs_status", "outbound_message_jobs", type_="check")
    op.create_check_constraint(
        "outbound_message_jobs_status",
        "outbound_message_jobs",
        "status IN ('pending', 'processing', 'delivered', 'failed')",
    )
    op.drop_constraint("delivery_status", "messages", type_="check")
    op.create_check_constraint(
        "delivery_status",
        "messages",
        "delivery_status IN ('pending', 'sent', 'failed')",
    )
