"""billing gate determinístico — novo estado de conversa + colunas de suporte

Adiciona o estado `billing_gate` a `conversations.state` (rollout gradual,
controlado por tenant_billing_settings.insufficient_balance_policy — ver
docs/superpowers/specs/2026-07-22-billing-gate-deterministico-design.md) e as
colunas de apoio pra máquina de estados conduzida pelo worker: qual step do
gate a conversa está, quantas tentativas não reconhecidas já teve, e o link
de pagamento já gerado (reenviado, nunca recriado, enquanto aguarda o
pagamento). `tenant_billing_settings` ganha um texto de boas-vindas
opcional pro tenant customizar (cai num texto genérico se não configurado).

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-22
"""

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("state", "conversations", type_="check")
    op.create_check_constraint(
        "state",
        "conversations",
        "state IN ('agent', 'human', 'billing_gate')",
    )
    op.add_column(
        "conversations",
        sa.Column("billing_gate_step", sa.String(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "billing_gate_retries", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    op.add_column(
        "conversations",
        sa.Column("billing_gate_checkout_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "tenant_billing_settings",
        sa.Column("billing_gate_welcome_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    # ⚠️ create_check_constraint abaixo falha se alguma conversation ainda
    # estiver em state='billing_gate' no momento do downgrade (violação da
    # constraint restrita) — só rodar depois de confirmar que nenhum tenant
    # em insufficient_balance_policy='deterministic_gate' tem gate aberto.
    op.drop_column("tenant_billing_settings", "billing_gate_welcome_text")
    op.drop_column("conversations", "billing_gate_checkout_url")
    op.drop_column("conversations", "billing_gate_retries")
    op.drop_column("conversations", "billing_gate_step")
    op.drop_constraint("state", "conversations", type_="check")
    op.create_check_constraint("state", "conversations", "state IN ('agent', 'human')")
