"""adiciona estado operacional do atendimento

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-21
"""

import sqlalchemy as sa

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "automation_status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'idle'"),
        ),
    )
    op.create_check_constraint(
        "automation_status",
        "conversations",
        "automation_status IN ('idle', 'processing', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint("automation_status", "conversations", type_="check")
    op.drop_column("conversations", "automation_status")
