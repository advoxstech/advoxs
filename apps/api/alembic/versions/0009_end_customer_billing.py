"""cobrança do cliente final: settings/pacotes/saldo/ledger por tenant + sender_type system

Segunda camada de billing (cliente final -> tenant), independente do billing
tenant->plataforma já existente. Cada tenant guarda a própria secret key/webhook
secret da Stripe (cifradas) e define os próprios pacotes de crédito pros clientes.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-13
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = [
    "tenant_billing_settings",
    "end_customer_credit_packages",
    "end_customer_balances",
    "end_customer_credit_transactions",
]


def upgrade() -> None:
    op.create_table(
        "tenant_billing_settings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("billing_mode", sa.String(), server_default=sa.text("'credits'"), nullable=False),
        sa.Column("stripe_secret_key_encrypted", sa.Text(), nullable=True),
        sa.Column("stripe_webhook_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("end_customer_tokens_per_credit", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_billing_settings_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_billing_settings")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_tenant_billing_settings_tenant_id")),
    )

    op.create_table(
        "end_customer_credit_packages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("price_brl", sa.Numeric(10, 2), nullable=False),
        sa.Column("credits_granted", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_end_customer_credit_packages_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_end_customer_credit_packages")),
    )
    op.create_index(
        op.f("ix_end_customer_credit_packages_tenant_id"),
        "end_customer_credit_packages",
        ["tenant_id"],
    )

    op.create_table(
        "end_customer_balances",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_phone_number", sa.String(), nullable=False),
        sa.Column("credit_balance", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_end_customer_balances_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_end_customer_balances")),
        sa.UniqueConstraint(
            "tenant_id", "contact_phone_number", name=op.f("uq_end_customer_balances_tenant_id")
        ),
    )

    op.create_table(
        "end_customer_credit_transactions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("contact_phone_number", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("amount_credits", sa.Integer(), nullable=False),
        sa.Column("end_customer_credit_package_id", sa.Uuid(), nullable=True),
        sa.Column("related_message_id", sa.Uuid(), nullable=True),
        sa.Column("stripe_payment_id", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "type IN ('purchase', 'consumption')",
            name=op.f("ck_end_customer_credit_transactions_type"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_end_customer_credit_transactions_tenant_id_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["end_customer_credit_package_id"],
            ["end_customer_credit_packages.id"],
            name="fk_end_customer_credit_transactions_package_id",
        ),
        sa.ForeignKeyConstraint(
            ["related_message_id"],
            ["messages.id"],
            name=op.f("fk_end_customer_credit_transactions_related_message_id_messages"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_end_customer_credit_transactions")),
        sa.UniqueConstraint(
            "stripe_payment_id", name=op.f("uq_end_customer_credit_transactions_stripe_payment_id")
        ),
    )
    op.create_index(
        op.f("ix_end_customer_credit_transactions_tenant_id"),
        "end_customer_credit_transactions",
        ["tenant_id"],
    )

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )

    op.drop_constraint("sender_type", "messages", type_="check")
    op.create_check_constraint(
        "sender_type", "messages", "sender_type IN ('agent', 'human', 'contact', 'system')"
    )


def downgrade() -> None:
    op.drop_constraint("sender_type", "messages", type_="check")
    op.create_check_constraint(
        "sender_type", "messages", "sender_type IN ('agent', 'human', 'contact')"
    )

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("end_customer_credit_transactions")
    op.drop_table("end_customer_balances")
    op.drop_table("end_customer_credit_packages")
    op.drop_table("tenant_billing_settings")
