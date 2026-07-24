"""Stripe Connect (Accounts v2) para a cobrança do cliente final — tenant
deixa de colar secret key/webhook secret da própria conta Stripe; passa a
configurar tudo via onboarding embutido no painel Advoxs. billing_provider
decide, por tenant, qual caminho o checkout/webhook seguem — ver
docs/superpowers/specs/2026-07-24-stripe-connect-cobranca-cliente-final-design.md.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-24
"""

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_billing_settings",
        sa.Column(
            "billing_provider",
            sa.String(),
            server_default=sa.text("'standalone'"),
            nullable=False,
        ),
    )
    op.add_column(
        "tenant_billing_settings",
        sa.Column("stripe_account_id", sa.String(), nullable=True),
    )
    op.add_column(
        "tenant_billing_settings",
        sa.Column("stripe_account_status", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_billing_settings", "stripe_account_status")
    op.drop_column("tenant_billing_settings", "stripe_account_id")
    op.drop_column("tenant_billing_settings", "billing_provider")
