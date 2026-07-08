"""unique (tenant_id, filename) em knowledge_base_files

Backstop no banco da checagem de duplicado do upload: a rota faz
check-then-insert, então dois uploads concorrentes com o mesmo filename
passariam ambos pelo 409 — a constraint garante que só um commita.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-08
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_knowledge_base_files_tenant_filename",
        "knowledge_base_files",
        ["tenant_id", "filename"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_knowledge_base_files_tenant_filename",
        "knowledge_base_files",
        type_="unique",
    )
