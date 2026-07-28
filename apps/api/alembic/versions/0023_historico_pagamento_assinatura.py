"""Histórico de pagamento de assinatura mensal recorrente do cliente final —
1 linha por invoice pago (criação + cada renovação), espelhando o que compra
avulsa já faz em end_customer_credit_transactions. Alimenta o relatório de
faturamento — ver docs/superpowers/specs/2026-07-28-dashboard-financeiro-tenant-design.md.

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-28
"""

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = ["end_customer_subscription_payments"]


def upgrade() -> None:
    op.create_table(
        "end_customer_subscription_payments",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("contact_phone_number", sa.String(), nullable=False),
        sa.Column(
            "end_customer_subscription_id",
            sa.Uuid(),
            sa.ForeignKey("end_customer_subscriptions.id"),
            nullable=False,
        ),
        sa.Column("amount_brl", sa.Numeric(10, 2), nullable=False),
        sa.Column("stripe_invoice_id", sa.String(), nullable=False, unique=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
    )

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("end_customer_subscription_payments")
