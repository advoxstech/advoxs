"""Importação manual do Drive e preparação segura de atualizações.

Revision ID: 0040
Revises: 0039
"""

import sqlalchemy as sa

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("drive_file_id", sa.String(200), nullable=True),
        sa.Column("drive_version", sa.String(100), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaces_file_id", sa.Uuid(), nullable=True),
    ):
        op.add_column("knowledge_base_files", column)
    op.create_foreign_key(
        "fk_knowledge_base_files_replaces_file_id_knowledge_base_files",
        "knowledge_base_files",
        "knowledge_base_files",
        ["replaces_file_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "uq_knowledge_base_files_tenant_filename", "knowledge_base_files", type_="unique"
    )
    active = sa.text("superseded_at IS NULL AND replaces_file_id IS NULL")
    op.create_index(
        "uq_knowledge_base_files_tenant_filename",
        "knowledge_base_files",
        ["tenant_id", "filename"],
        unique=True,
        postgresql_where=active,
    )
    op.create_index(
        "uq_kb_drive_active",
        "knowledge_base_files",
        ["tenant_id", "drive_file_id"],
        unique=True,
        postgresql_where=active,
    )
    op.create_index(
        "uq_kb_pending_replacement",
        "knowledge_base_files",
        ["replaces_file_id"],
        unique=True,
    )


def downgrade() -> None:
    # Recusa perda silenciosa de histórico e conflitos de nomes no rollback.
    count = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM knowledge_base_files "
            "WHERE superseded_at IS NOT NULL OR replaces_file_id IS NOT NULL"
        )
    )
    if count:
        raise RuntimeError("Existem versões do Drive; preserve os dados antes do downgrade.")
    for name in (
        "uq_kb_pending_replacement",
        "uq_kb_drive_active",
        "uq_knowledge_base_files_tenant_filename",
    ):
        op.drop_index(name, table_name="knowledge_base_files")
    op.create_unique_constraint(
        "uq_knowledge_base_files_tenant_filename", "knowledge_base_files", ["tenant_id", "filename"]
    )
    op.drop_constraint(
        "fk_knowledge_base_files_replaces_file_id_knowledge_base_files",
        "knowledge_base_files",
        type_="foreignkey",
    )
    for name in (
        "replaces_file_id",
        "superseded_at",
        "imported_at",
        "content_sha256",
        "drive_version",
        "drive_file_id",
    ):
        op.drop_column("knowledge_base_files", name)
