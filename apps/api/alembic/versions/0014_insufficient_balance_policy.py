"""politica de saldo insuficiente do cliente final (hook, default block_with_message)

Único valor suportado por ora — mesmo padrão do `billing_mode` (hook de
extensibilidade sem mudar comportamento).

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-17
"""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_billing_settings",
        sa.Column(
            "insufficient_balance_policy",
            sa.String(),
            server_default=sa.text("'block_with_message'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_billing_settings", "insufficient_balance_policy")
