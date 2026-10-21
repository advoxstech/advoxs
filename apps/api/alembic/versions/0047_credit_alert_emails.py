"""E-mail alerts to office admins when platform credits run low or out.

Revision ID: 0047
Revises: 0046
"""

from pathlib import Path

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text().split("\n-- statement\n"):
        op.execute(statement)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS team_queue_credit_alert ON tenants")
    op.execute("DROP FUNCTION IF EXISTS team_queue_credit_alert()")
    op.execute("DROP FUNCTION IF EXISTS low_credits_threshold(uuid)")
    op.execute("DELETE FROM team_emails WHERE kind IN ('low_credits','credits_depleted')")
    op.execute("ALTER TABLE team_emails DROP CONSTRAINT IF EXISTS team_emails_kind_check")
    op.execute(
        "ALTER TABLE team_emails ADD CONSTRAINT team_emails_kind_check"
        " CHECK (kind IN ('invitation','urgency'))"
    )
