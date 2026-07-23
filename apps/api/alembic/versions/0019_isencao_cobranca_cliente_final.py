"""isenção de cobrança por cliente final — flag por conversa

Adiciona conversations.end_customer_billing_exempt: quando true, o turno é
sempre custeado pelo TENANT (nunca pelo saldo do cliente final), e nem o
billing gate determinístico (apps/worker/app/billing_gate.py) nem o gate
antigo embutido no agents são acionados pro contato — ver
docs/superpowers/specs/2026-07-23-isencao-cobranca-cliente-final-design.md.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-23
"""

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "end_customer_billing_exempt",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "end_customer_billing_exempt")
