"""Distingue queda detectada de desconexão solicitada pelo escritório."""

import sqlalchemy as sa

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("whatsapp_numbers", sa.Column("zapi_disconnected_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("whatsapp_numbers", "zapi_disconnected_at")
