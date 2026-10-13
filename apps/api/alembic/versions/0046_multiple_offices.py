"""Shared identities with independent office memberships.

Revision ID: 0046
Revises: 0045
"""

from pathlib import Path

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text().split("\n-- statement\n"):
        op.execute(statement)


def downgrade():
    # A single-office schema cannot represent the new data without losing access.
    raise RuntimeError("0046 requires a coordinated backup restore; automatic downgrade is unsafe.")
