"""planos de assinatura (agentes, ferramentas reservadas, KB, créditos mensais)

Introduz subscription_plans (global) e tenant_subscriptions (tenant-scoped,
1:1) — ver docs/superpowers/specs/2026-07-21-planos-assinatura-design.md.
Backfill: todo tenant já existente ganha uma tenant_subscriptions apontando
pro plano "Legado" (sem limite algum, price_brl=0) — preserva o
comportamento de hoje (sem teto de agentes/KB) pra quem já é cliente; só
cadastros novos, a partir deste deploy, ganham uma assinatura de verdade
(ver app/services/default_subscription.py).

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-21
"""

import uuid

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = ["tenant_subscriptions"]

subscription_plans = sa.table(
    "subscription_plans",
    sa.column("id", sa.Uuid()),
    sa.column("name", sa.String()),
    sa.column("price_brl", sa.Numeric(10, 2)),
    sa.column("max_agents", sa.Integer()),
    sa.column("max_extra_tools", sa.Integer()),
    sa.column("max_knowledge_base_files", sa.Integer()),
    sa.column("max_knowledge_base_storage_bytes", sa.BigInteger()),
    sa.column("monthly_credits_granted", sa.Integer()),
    sa.column("is_legacy", sa.Boolean()),
    sa.column("active", sa.Boolean()),
)

ESSENCIAL_ID = uuid.uuid4()
PROFISSIONAL_ID = uuid.uuid4()
COMPLETO_ID = uuid.uuid4()
LEGADO_ID = uuid.uuid4()

MB = 1024 * 1024

PLANS = [
    {
        "id": ESSENCIAL_ID,
        "name": "Essencial",
        "price_brl": "97.00",
        "max_agents": 5,
        "max_extra_tools": 0,
        "max_knowledge_base_files": 50,
        "max_knowledge_base_storage_bytes": 250 * MB,
        "monthly_credits_granted": 300,
        "is_legacy": False,
        "active": True,
    },
    {
        "id": PROFISSIONAL_ID,
        "name": "Profissional",
        "price_brl": "247.00",
        "max_agents": 12,
        "max_extra_tools": 3,
        "max_knowledge_base_files": 150,
        "max_knowledge_base_storage_bytes": 750 * MB,
        "monthly_credits_granted": 1000,
        "is_legacy": False,
        "active": True,
    },
    {
        "id": COMPLETO_ID,
        "name": "Escritório Completo",
        "price_brl": "497.00",
        "max_agents": 30,
        "max_extra_tools": 8,
        "max_knowledge_base_files": 400,
        "max_knowledge_base_storage_bytes": 1536 * MB,
        "monthly_credits_granted": 3000,
        "is_legacy": False,
        "active": True,
    },
    {
        "id": LEGADO_ID,
        "name": "Legado",
        "price_brl": "0.00",
        "max_agents": None,
        "max_extra_tools": None,
        "max_knowledge_base_files": None,
        "max_knowledge_base_storage_bytes": None,
        "monthly_credits_granted": 0,
        "is_legacy": True,
        "active": True,
    },
]


def upgrade() -> None:
    op.create_table(
        "subscription_plans",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("price_brl", sa.Numeric(10, 2), nullable=False),
        sa.Column("max_agents", sa.Integer(), nullable=True),
        sa.Column("max_extra_tools", sa.Integer(), nullable=True),
        sa.Column("max_knowledge_base_files", sa.Integer(), nullable=True),
        sa.Column("max_knowledge_base_storage_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "monthly_credits_granted", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("is_legacy", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscription_plans")),
    )

    op.create_table(
        "tenant_subscriptions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('active', 'past_due', 'canceled')",
            name=op.f("ck_tenant_subscriptions_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_subscriptions_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["subscription_plans.id"],
            name=op.f("fk_tenant_subscriptions_plan_id_subscription_plans"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_subscriptions")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_tenant_subscriptions_tenant_id")),
        sa.UniqueConstraint(
            "stripe_subscription_id", name=op.f("uq_tenant_subscriptions_stripe_subscription_id")
        ),
    )
    op.create_index(
        op.f("ix_tenant_subscriptions_tenant_id"), "tenant_subscriptions", ["tenant_id"]
    )

    op.bulk_insert(subscription_plans, PLANS)

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )

    op.execute(
        "INSERT INTO tenant_subscriptions (id, tenant_id, plan_id, status) "
        f"SELECT gen_random_uuid(), id, '{LEGADO_ID}', 'active' FROM tenants"
    )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("tenant_subscriptions")
    op.drop_table("subscription_plans")
