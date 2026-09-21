"""guarda credenciais de webhook Meta por tenant

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-21
"""

import sqlalchemy as sa

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_numbers",
        sa.Column("meta_app_secret_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "whatsapp_numbers",
        sa.Column("meta_webhook_secret", sa.String(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_whatsapp_numbers_meta_webhook_secret",
        "whatsapp_numbers",
        ["meta_webhook_secret"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_whatsapp_numbers_meta_webhook_secret",
        "whatsapp_numbers",
        type_="unique",
    )
    op.drop_column("whatsapp_numbers", "meta_webhook_secret")
    op.drop_column("whatsapp_numbers", "meta_app_secret_encrypted")
