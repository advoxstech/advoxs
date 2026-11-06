"""Manual follow-up batches sent after the WhatsApp customer service window.

Revision ID: 0049
Revises: 0048
"""

from pathlib import Path

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text().split("\n-- statement\n"):
        op.execute(statement)


def downgrade():
    op.execute("DROP TABLE follow_up_recipients, follow_up_batches")
