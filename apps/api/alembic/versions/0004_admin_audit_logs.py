"""tabela admin_audit_logs (auditoria de leitura de tenant específico)

Toda chamada a GET /platform-admin/tenants/{id} grava uma linha aqui —
implementa a exigência de auditoria do CLAUDE.md (super-admin lendo dado de
um tenant específico atravessa o isolamento normal por tenant_id).

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-09
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("platform_admin_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["platform_admin_id"],
            ["platform_admins.id"],
            name=op.f("fk_admin_audit_logs_platform_admin_id_platform_admins"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_admin_audit_logs_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit_logs")),
    )
    op.create_index(
        op.f("ix_admin_audit_logs_platform_admin_id"), "admin_audit_logs", ["platform_admin_id"]
    )
    op.create_index(op.f("ix_admin_audit_logs_tenant_id"), "admin_audit_logs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_audit_logs_tenant_id"), table_name="admin_audit_logs")
    op.drop_index(op.f("ix_admin_audit_logs_platform_admin_id"), table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")
