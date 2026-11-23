"""Especialidade dos agentes existentes deduzida do nome.

A 0053 só reconheceu os nomes dos agentes padrão (Condominial, Contratos,
Direito do Consumidor). Escritórios que criaram agentes com o nome da área
("Cível", "Trabalhista", "Direito Tributário"...) ficaram sem especialidade.
Preenche só agentes sem especialidade e que não são o ponto de entrada.

Regra congelada aqui de propósito (cópia de app/core/knowledge_tree.py em
2026-11-11): mudar a regra do app não pode mudar o que esta migration fez.

Os pacotes não são entregues aqui (a migration não enfileira cópias): depois
do deploy, rodar scripts/load_public_base.py com --provision, ou usar
"Restaurar pacote" no painel.

Revision ID: 0054
Revises: 0053
"""

import unicodedata

import sqlalchemy as sa

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

_NAMES = {
    "civel": ("civel", "civil"),
    "familia_sucessoes": ("familia sucessoes", "familia"),
    "trabalhista": ("trabalhista", "trabalho"),
    "previdenciario": ("previdenciario",),
    "consumidor": ("consumidor",),
    "tributario": ("tributario",),
    "empresarial": ("empresarial",),
    "penal": ("penal", "criminal"),
    "imobiliario": ("imobiliario",),
    "saude": ("saude",),
    "bancario_financeiro": ("bancario financeiro", "bancario"),
    "digital_protecao_dados": ("digital protecao dados", "digital", "protecao dados"),
    "administrativo_publico": ("administrativo publico", "administrativo"),
    "condominial": ("condominial",),
    "contratos": ("contratos", "contratual"),
    "ambiental": ("ambiental",),
}
_STOPWORDS = {"direito", "do", "da", "de", "dos", "das", "e"}
_BY_NAME = {alias: specialty for specialty, aliases in _NAMES.items() for alias in aliases}


def _key(name: str) -> str:
    text = unicodedata.normalize("NFD", name.lower())
    text = "".join(c if c.isalnum() else " " for c in text if unicodedata.category(c) != "Mn")
    return " ".join(word for word in text.split() if word not in _STOPWORDS)


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, name FROM agents WHERE specialty IS NULL AND is_entry_point = false")
    ).all()
    for agent_id, name in rows:
        specialty = _BY_NAME.get(_key(name or ""))
        if specialty:
            conn.execute(
                sa.text("UPDATE agents SET specialty = :specialty WHERE id = :id"),
                {"specialty": specialty, "id": agent_id},
            )


def downgrade() -> None:
    # Não dá para distinguir o que esta migration preencheu do que o escritório
    # escolheu depois; a especialidade fica.
    pass
