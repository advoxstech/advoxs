"""fundação da wallet unificada: pricing_configs, saldos fracionados,
tipos resale/adjustment e auditoria de tokens nos ledgers

Etapa 1 do modelo de moeda única — nenhuma mudança de comportamento:
a conversão tokens->créditos continua na env até a Etapa 2.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-17
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

LEDGERS = ["credit_transactions", "end_customer_credit_transactions"]
# Curto de propósito: o nome por convenção estoura os 63 chars do Postgres.
FK_END_CUSTOMER_PRICING = "fk_end_customer_credit_transactions_pricing_config_id"


def upgrade() -> None:
    # Config global de pricing, versionada. Não tenant-scoped (como
    # credit_packages) — sem RLS; grants vêm dos DEFAULT PRIVILEGES da 0008.
    op.create_table(
        "pricing_configs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tokens_per_credit", sa.Integer(), nullable=False),
        sa.Column("input_weight", sa.Numeric(6, 4), nullable=False),
        sa.Column("output_weight", sa.Numeric(6, 4), nullable=False),
        sa.Column(
            "effective_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pricing_configs")),
    )
    # Config inicial: 1 crédito = 1000 tokens ponderados, pesos 0.3/1.0.
    op.execute(
        "INSERT INTO pricing_configs (tokens_per_credit, input_weight, output_weight) "
        "VALUES (1000, 0.3, 1.0)"
    )

    # Créditos fracionados (4 casas) em saldos e ledgers. int->numeric é lossless.
    op.alter_column(
        "tenants",
        "credit_balance",
        type_=sa.Numeric(12, 4),
        existing_type=sa.Integer(),
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )
    op.alter_column(
        "end_customer_balances",
        "credit_balance",
        type_=sa.Numeric(12, 4),
        existing_type=sa.Integer(),
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )
    op.alter_column(
        "credit_transactions",
        "amount_credits",
        type_=sa.Numeric(12, 4),
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.alter_column(
        "end_customer_credit_transactions",
        "amount_credits",
        type_=sa.Numeric(12, 4),
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.alter_column(
        "messages",
        "credits_consumed",
        type_=sa.Numeric(12, 4),
        existing_type=sa.Numeric(12, 2),
        existing_nullable=True,
    )

    # Tipos novos de lançamento nos dois ledgers (mesmo padrão da 0009 com
    # sender_type: o nome curto expande pela naming convention).
    op.drop_constraint("type", "credit_transactions", type_="check")
    op.create_check_constraint(
        "type",
        "credit_transactions",
        "type IN ('purchase', 'consumption', 'refund', 'bonus', 'resale', 'adjustment')",
    )
    op.drop_constraint("type", "end_customer_credit_transactions", type_="check")
    op.create_check_constraint(
        "type",
        "end_customer_credit_transactions",
        "type IN ('purchase', 'consumption', 'resale', 'adjustment')",
    )

    # Auditoria de consumo: tokens brutos + config vigente no momento do débito.
    for table in LEDGERS:
        op.add_column(table, sa.Column("tokens_input", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("tokens_output", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("pricing_config_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_credit_transactions_pricing_config_id_pricing_configs"),
        "credit_transactions",
        "pricing_configs",
        ["pricing_config_id"],
        ["id"],
    )
    op.create_foreign_key(
        FK_END_CUSTOMER_PRICING,
        "end_customer_credit_transactions",
        "pricing_configs",
        ["pricing_config_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        FK_END_CUSTOMER_PRICING, "end_customer_credit_transactions", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_credit_transactions_pricing_config_id_pricing_configs"),
        "credit_transactions",
        type_="foreignkey",
    )
    for table in LEDGERS:
        op.drop_column(table, "pricing_config_id")
        op.drop_column(table, "tokens_output")
        op.drop_column(table, "tokens_input")

    op.drop_constraint("type", "end_customer_credit_transactions", type_="check")
    op.create_check_constraint(
        "type", "end_customer_credit_transactions", "type IN ('purchase', 'consumption')"
    )
    op.drop_constraint("type", "credit_transactions", type_="check")
    op.create_check_constraint(
        "type",
        "credit_transactions",
        "type IN ('purchase', 'consumption', 'refund', 'bonus')",
    )

    op.alter_column(
        "messages",
        "credits_consumed",
        type_=sa.Numeric(12, 2),
        existing_type=sa.Numeric(12, 4),
        existing_nullable=True,
    )
    op.alter_column(
        "end_customer_credit_transactions",
        "amount_credits",
        type_=sa.Integer(),
        existing_type=sa.Numeric(12, 4),
        postgresql_using="amount_credits::integer",
        existing_nullable=False,
    )
    op.alter_column(
        "credit_transactions",
        "amount_credits",
        type_=sa.Integer(),
        existing_type=sa.Numeric(12, 4),
        postgresql_using="amount_credits::integer",
        existing_nullable=False,
    )
    op.alter_column(
        "end_customer_balances",
        "credit_balance",
        type_=sa.Integer(),
        existing_type=sa.Numeric(12, 4),
        postgresql_using="credit_balance::integer",
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )
    op.alter_column(
        "tenants",
        "credit_balance",
        type_=sa.Integer(),
        existing_type=sa.Numeric(12, 4),
        postgresql_using="credit_balance::integer",
        existing_nullable=False,
        existing_server_default=sa.text("0"),
    )
    op.drop_table("pricing_configs")
