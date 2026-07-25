"""Assinatura mensal recorrente pro cliente final — end_customer_credit_packages
ganha `kind` ("one_time"|"subscription"); credits_granted deixa de ser
obrigatório (só faz sentido pra kind="one_time"); tabela nova
end_customer_subscriptions guarda o ciclo de vida da assinatura ativa por
contato — ver docs/superpowers/specs/2026-07-25-assinatura-recorrente-cliente-final-design.md.

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-25
"""

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = [
    "end_customer_subscriptions",
]


def upgrade() -> None:
    op.add_column(
        "end_customer_credit_packages",
        sa.Column("kind", sa.String(), server_default=sa.text("'one_time'"), nullable=False),
    )
    op.alter_column("end_customer_credit_packages", "credits_granted", nullable=True)

    op.create_table(
        "end_customer_subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("contact_phone_number", sa.String(), nullable=False),
        sa.Column(
            "end_customer_credit_package_id",
            sa.Uuid(),
            sa.ForeignKey("end_customer_credit_packages.id"),
            nullable=True,
        ),
        sa.Column("stripe_subscription_id", sa.String(), nullable=False, unique=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_end_customer_subscriptions_tenant_contact",
        "end_customer_subscriptions",
        ["tenant_id", "contact_phone_number"],
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

    op.drop_constraint(
        "uq_end_customer_subscriptions_tenant_contact",
        "end_customer_subscriptions",
        type_="unique",
    )
    op.drop_table("end_customer_subscriptions")
    op.alter_column("end_customer_credit_packages", "credits_granted", nullable=False)
    op.drop_column("end_customer_credit_packages", "kind")
