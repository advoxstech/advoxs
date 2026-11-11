"""Árvore da base de conhecimento: pastas (categorias), especialidades e pacotes.

Os valores são os mesmos dos CHECK constraints da migration 0053 e de
apps/web/src/lib/kb-categories.ts (mantidos em sincronia manualmente).
"""

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

# Tenant reservado no api_rag para a base central da Advoxs: cada documento é
# lido uma vez aqui e copiado, já indexado, para os escritórios.
PUBLIC_TENANT_ID = "advoxs-public"
