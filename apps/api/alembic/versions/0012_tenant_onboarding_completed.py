"""onboarding_completed_at em tenants

Tutorial de primeira abertura: NULL = tenant ainda não viu o wizard de
boas-vindas. Backfill marca todos os tenants existentes como completados —
só conta criada depois deste deploy vê o tutorial.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-16
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE tenants SET onboarding_completed_at = now()")


def downgrade() -> None:
    op.drop_column("tenants", "onboarding_completed_at")
