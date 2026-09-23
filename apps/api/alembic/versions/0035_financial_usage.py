"""registra custo operacional e valores históricos

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-22
"""

import sqlalchemy as sa

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("credit_transactions", sa.Column("amount_brl", sa.Numeric(10, 2)))
    op.add_column("end_customer_credit_transactions", sa.Column("amount_brl", sa.Numeric(10, 2)))
    op.create_table(
        "usage_records",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("related_message_id", sa.Uuid(), nullable=False),
        sa.Column("contact_phone_number", sa.String(), nullable=False),
        sa.Column("funding_source", sa.String(), nullable=False),
        sa.Column("operational_credits", sa.Numeric(12, 4), nullable=False),
        sa.Column("billed_credits", sa.Numeric(12, 4), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "shortfall_credits", sa.Numeric(12, 4), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "document_credits", sa.Numeric(12, 4), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("tokens_input", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_output", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("pricing_config_id", sa.Uuid()),
        sa.Column("end_customer_subscription_id", sa.Uuid()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "funding_source IN ('tenant', 'end_customer_credits', 'end_customer_subscription')",
            name="funding_source",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(
            ["end_customer_subscription_id"], ["end_customer_subscriptions.id"]
        ),
        sa.ForeignKeyConstraint(["pricing_config_id"], ["pricing_configs.id"]),
        sa.ForeignKeyConstraint(["related_message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("related_message_id"),
    )
    op.create_index("ix_usage_records_tenant_id", "usage_records", ["tenant_id"])
    op.execute("ALTER TABLE usage_records ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON usage_records "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON usage_records")
    op.execute("ALTER TABLE usage_records DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_usage_records_tenant_id", table_name="usage_records")
    op.drop_table("usage_records")
    op.drop_column("end_customer_credit_transactions", "amount_brl")
    op.drop_column("credit_transactions", "amount_brl")
