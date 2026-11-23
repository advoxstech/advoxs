"""Árvore da base de conhecimento: pastas (categorias), especialidades e pacotes.

Os valores são os mesmos dos CHECK constraints da migration 0053 e de
apps/web/src/lib/kb-categories.ts (mantidos em sincronia manualmente).
"""

import unicodedata

KB_CATEGORIES = (
    "livros_digitais",
    "artigos_cientificos",
    "processos_judiciais",
    "materias_jornalisticas",
    "modelos",
    "modelos_contratos",
    "pecas_processuais",
    "nao_selecionaveis",
    "constituicao",
    "legislacao_especifica",
    "decisoes_tribunais",
    "area_3",
    "area_4",
    "area_5",
    "area_6",
)

# Pasta F: o escritório guarda o arquivo, mas o agente não o consulta.
UNREADABLE_CATEGORY = "nao_selecionaveis"

SPECIALTIES = (
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

# Nomes de agente que indicam a especialidade (comparados sem acento, sem caixa e
# sem "direito", "do", "da", "de", "e"). Só nome exato: "Contratos Imobiliários"
# não vira nenhuma das duas.
_SPECIALTY_NAMES = {
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
_NAME_STOPWORDS = {"direito", "do", "da", "de", "dos", "das", "e"}


def _name_key(name: str) -> str:
    text = unicodedata.normalize("NFD", name.lower())
    text = "".join(c if c.isalnum() else " " for c in text if unicodedata.category(c) != "Mn")
    return " ".join(word for word in text.split() if word not in _NAME_STOPWORDS)


_SPECIALTY_BY_NAME = {
    alias: specialty for specialty, aliases in _SPECIALTY_NAMES.items() for alias in aliases
}


def specialty_from_name(name: str) -> str | None:
    """Especialidade indicada pelo nome do agente ("Direito Tributário" → tributario)."""
    return _SPECIALTY_BY_NAME.get(_name_key(name))


# Tenant reservado no api_rag para a base central da Advoxs: cada documento é
# lido uma vez aqui e copiado, já indexado, para os escritórios.
PUBLIC_TENANT_ID = "advoxs-public"
