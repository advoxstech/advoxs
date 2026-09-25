"""Keep response evidence with the message, under its existing tenant RLS.

Revision ID: 0039
Revises: 0038
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("response_sources", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "response_sources")
