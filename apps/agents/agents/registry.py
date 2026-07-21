"""Metadados das TOOLS genéricas disponíveis, para o endpoint GET /agents.

Desde a Etapa 2 (agentes por tenant), não existe mais uma lista fixa de
"agentes da plataforma" — cada tenant define os próprios via a tabela
`agents` do `api`. Este registro passou a listar só as tools genéricas do
grafo (o mesmo conjunto para todo tenant), não confundir com essa tabela.
"""

from agents.tools import tools as agent_tools

AGENTS_REGISTRY = {
    "tools": [
        {"name": tool.name, "description": tool.description}
        for tool in agent_tools
    ],
}
