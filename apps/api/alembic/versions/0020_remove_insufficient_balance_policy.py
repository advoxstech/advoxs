"""remove insufficient_balance_policy — gate único determinístico

O billing gate determinístico deixou de ser opcional: não existe mais
rollout gradual por tenant, é o único mecanismo quando
tenant_billing_settings.enabled = true. A coluna que decidia entre os dois
mecanismos (`block_with_message` | `deterministic_gate`) não tem mais
nenhum valor possível além de um só comportamento, então é removida — ver
docs/superpowers/specs/2026-07-23-gate-unico-deterministico-design.md.

`billing_gate_welcome_text` (mesma tabela) permanece — é customização de
texto do tenant, não decide mecanismo.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-23
"""

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("tenant_billing_settings", "insufficient_balance_policy")


def downgrade() -> None:
    op.add_column(
        "tenant_billing_settings",
        sa.Column(
            "insufficient_balance_policy",
            sa.String(),
            server_default=sa.text("'block_with_message'"),
            nullable=False,
        ),
    )
