from agents.tools import tools as agent_tools

AGENTS_REGISTRY = [
    {
        "name": "Agente Condominial",
        "description": "Agente principal de atendimento condominial",
        "available": True,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "available": True,
            }
            for tool in agent_tools
        ],
    }
]
