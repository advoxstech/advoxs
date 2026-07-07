"""add tenant_id em documentos_usuario

Revision ID: a1b2c3d4e5f6
Revises: c32de736dab0
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "c32de736dab0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: linhas pré-multi-tenancy não têm dono conhecido — precisam ser
    # migradas manualmente (ou re-ingeridas) antes de servir tenants reais.
    op.add_column("documentos_usuario", sa.Column("tenant_id", sa.String(), nullable=True))
    op.create_index("ix_documentos_usuario_tenant_id", "documentos_usuario", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_documentos_usuario_tenant_id", table_name="documentos_usuario")
    op.drop_column("documentos_usuario", "tenant_id")
