"""Pedido humano independente de urgência, com outbox e auditoria.

Revision ID: 0057
Revises: 0056
"""

from pathlib import Path

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text().split("\n-- statement\n"):
        op.execute(statement)


def downgrade():
    op.execute("DROP TRIGGER contact_requests_human ON messages")
    op.execute("DROP FUNCTION contact_requests_human()")
    op.execute("DROP FUNCTION requests_human_attendance(text)")
    op.execute("DROP TRIGGER team_human_request ON conversations")
    op.execute("DROP FUNCTION team_queue_human_request()")
    op.execute("DELETE FROM team_emails WHERE kind='human_request'")
    op.execute("ALTER TABLE team_emails DROP CONSTRAINT team_emails_kind_check")
    op.execute(
        "ALTER TABLE team_emails ADD CONSTRAINT team_emails_kind_check CHECK "
        "(kind IN ('invitation','urgency','low_credits','credits_depleted'))"
    )
    for column in (
        "human_requested_at",
        "human_request_reason",
        "human_request_agent_id",
        "human_request_episode_id",
    ):
        op.drop_column("conversations", column)
