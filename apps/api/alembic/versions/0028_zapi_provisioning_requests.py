"""zapi_provisioning_requests — pedido do tenant pra Advoxs configurar a
conexão Z-API por ele (fluxo manual, ver migration 0027 e app/services/
zapi_connection.py). Cada linha é um pedido isolado (sem UNIQUE por tenant):
guarda histórico e permite um novo pedido depois de um anterior já resolvido.

`status`: "pending" (aguardando) | "fulfilled" (admin provisionou a
instância) | "dismissed" (reservado pra descarte manual futuro — nenhuma
rota ainda gera esse valor).

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-12
"""

import sqlalchemy as sa

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

TENANT_SCOPED_TABLES = ["zapi_provisioning_requests"]


def upgrade() -> None:
    op.create_table(
        "zapi_provisioning_requests",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "status",
        "zapi_provisioning_requests",
        "status IN ('pending', 'fulfilled', 'dismissed')",
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
    op.drop_table("zapi_provisioning_requests")
