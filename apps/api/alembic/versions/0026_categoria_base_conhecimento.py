"""knowledge_base_files.category — organização por categoria (POP GVA Digital)

Categorias fixas, iguais pra todo tenant/agente (artigos científicos, matérias
jornalísticas, decisões de tribunais, livros digitais, processos judiciais,
modelos de contratos, peças processuais, não selecionáveis) — só organização/
visual na árvore de `/base-de-conhecimento` (uma subpasta por categoria dentro
de cada agente), não afeta a busca do agente (buscar_base_conhecimento_agente
continua vendo todos os arquivos anexados, sem filtro de categoria).

Nullable: arquivos já existentes (e novos uploads sem categoria escolhida)
ficam com category=NULL — "sem categoria" no front, não um erro.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-03
"""

import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_CATEGORIES = (
    "artigos_cientificos",
    "materias_jornalisticas",
    "decisoes_tribunais",
    "livros_digitais",
    "processos_judiciais",
    "modelos_contratos",
    "pecas_processuais",
    "nao_selecionaveis",
)


def upgrade() -> None:
    op.add_column("knowledge_base_files", sa.Column("category", sa.String(), nullable=True))
    values = ", ".join(f"'{c}'" for c in _CATEGORIES)
    op.create_check_constraint(
        "category",
        "knowledge_base_files",
        f"category IN ({values}) OR category IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("category", "knowledge_base_files", type_="check")
    op.drop_column("knowledge_base_files", "category")
