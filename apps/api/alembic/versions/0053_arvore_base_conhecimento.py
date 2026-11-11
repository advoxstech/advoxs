"""Árvore da base de conhecimento: pastas A–F e Z, especialidade do agente e pacotes.

- `knowledge_base_files.category` ganha as pastas da árvore (modelos, constituição,
  legislação específica e as quatro pastas da área, Z.3 a Z.6).
- `agents.specialty`: uma das 16 especialidades (ou NULL). Define os nomes de
  Z.3–Z.6 no painel e qual pacote de documentos o agente recebe.
- `public_documents` / `specialty_package_documents` (globais): a base central da
  Advoxs e a composição do pacote de cada especialidade.
- `knowledge_base_files.public_document_id`: marca a cópia de um documento do
  pacote no escritório (não conta no limite do plano).

Agentes padrão que ainda têm o nome original recebem a especialidade
correspondente; os documentos só chegam quando a base central é carregada
(scripts/load_public_base.py).

Revision ID: 0053
Revises: 0052
"""

import sqlalchemy as sa

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

_OLD_CATEGORIES = (
    "artigos_cientificos",
    "materias_jornalisticas",
    "decisoes_tribunais",
    "livros_digitais",
    "processos_judiciais",
    "modelos_contratos",
    "pecas_processuais",
    "nao_selecionaveis",
)
_NEW_CATEGORIES = (
    *_OLD_CATEGORIES,
    "modelos",
    "constituicao",
    "legislacao_especifica",
    "area_3",
    "area_4",
    "area_5",
    "area_6",
)
_SPECIALTIES = (
    "civel",
    "familia_sucessoes",
    "trabalhista",
    "previdenciario",
    "consumidor",
    "tributario",
    "empresarial",
    "penal",
    "imobiliario",
    "saude",
    "bancario_financeiro",
    "digital_protecao_dados",
    "administrativo_publico",
    "condominial",
    "contratos",
    "ambiental",
)
_DEFAULT_AGENT_SPECIALTIES = {
    "Condominial": "condominial",
    "Contratos": "contratos",
    "Direito do Consumidor": "consumidor",
}


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.drop_constraint("category", "knowledge_base_files", type_="check")
    op.create_check_constraint(
        "category",
        "knowledge_base_files",
        f"category IN ({_in(_NEW_CATEGORIES)}) OR category IS NULL",
    )

    op.add_column("agents", sa.Column("specialty", sa.String(), nullable=True))
    op.create_check_constraint(
        "specialty", "agents", f"specialty IN ({_in(_SPECIALTIES)}) OR specialty IS NULL"
    )
    for name, specialty in _DEFAULT_AGENT_SPECIALTIES.items():
        op.execute(
            sa.text(
                "UPDATE agents SET specialty = :specialty "
                "WHERE name = :name AND is_entry_point = false"
            ).bindparams(name=name, specialty=specialty)
        )

    op.create_table(
        "public_documents",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False, unique=True),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("pages_total", sa.Integer()),
        sa.Column("partial_pages", sa.ARRAY(sa.Integer())),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'processing'")),
        sa.Column("error_message", sa.Text()),
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
        sa.CheckConstraint("status IN ('processing', 'ready', 'error')", name="status"),
        sa.CheckConstraint(f"category IN ({_in(_NEW_CATEGORIES)})", name="category"),
    )
    op.create_table(
        "specialty_package_documents",
        sa.Column("specialty", sa.String(), primary_key=True),
        sa.Column(
            "public_document_id",
            sa.Uuid(),
            sa.ForeignKey("public_documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.CheckConstraint(f"specialty IN ({_in(_SPECIALTIES)})", name="specialty"),
    )
    op.create_index(
        "ix_specialty_package_documents_public_document_id",
        "specialty_package_documents",
        ["public_document_id"],
    )
    for table in ("public_documents", "specialty_package_documents"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO advoxs_app, advoxs_system")

    op.add_column(
        "knowledge_base_files",
        sa.Column(
            "public_document_id",
            sa.Uuid(),
            sa.ForeignKey("public_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_knowledge_base_files_public_document_id",
        "knowledge_base_files",
        ["public_document_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_base_files_public_document_id", "knowledge_base_files")
    op.drop_column("knowledge_base_files", "public_document_id")
    op.drop_table("specialty_package_documents")
    op.drop_table("public_documents")
    op.drop_constraint("specialty", "agents", type_="check")
    op.drop_column("agents", "specialty")
    op.execute(
        f"UPDATE knowledge_base_files SET category = NULL "
        f"WHERE category NOT IN ({_in(_OLD_CATEGORIES)})"
    )
    op.drop_constraint("category", "knowledge_base_files", type_="check")
    op.create_check_constraint(
        "category",
        "knowledge_base_files",
        f"category IN ({_in(_OLD_CATEGORIES)}) OR category IS NULL",
    )
