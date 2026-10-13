"""Office team, invitations, exclusive human ownership and durable email outbox.

Revision ID: 0045
Revises: 0044
"""

from pathlib import Path

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Separate statements: asyncpg does not accept multiple commands in prepare().
    sql = Path(__file__).with_suffix(".sql").read_text()
    for statement in sql.split("\n-- statement\n"):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER team_urgency ON conversations")
    op.execute("DROP FUNCTION team_queue_urgency()")
    for table in ("team_emails", "team_invitations", "agent_lawyers", "attendance_events"):
        op.drop_table(table)
    op.drop_column("messages", "sender_user_id")
    op.drop_column("messages", "sender_user_name")
    op.drop_column("conversations", "assigned_user_id")
    op.drop_column("conversations", "assigned_user_name")
    op.drop_column("conversations", "urgency_episode_id")
    op.drop_column("users", "active")
    op.execute("ALTER TABLE users DROP CONSTRAINT team_role")
    op.drop_constraint("users_tenant_id_id", "users", type_="unique")
    op.drop_constraint("agents_tenant_id_id", "agents", type_="unique")
