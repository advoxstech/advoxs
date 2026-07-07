import pytest
from agents.workflow import route_from_start, route_from_tool_node


# ──────────────────────────────────────────────
# route_from_start
# ──────────────────────────────────────────────

def test_route_from_start_sem_especialista_vai_para_secretaria():
    assert route_from_start({"current_specialist": None}) == "agente_secretaria"


def test_route_from_start_sem_chave_vai_para_secretaria():
    assert route_from_start({}) == "agente_secretaria"


@pytest.mark.parametrize("specialist", [
    "agente_condominial",
    "agente_contratos",
    "agente_direito_consumidor",
])
def test_route_from_start_com_especialista_roteia_direto(specialist):
    assert route_from_start({"current_specialist": specialist}) == specialist


# ──────────────────────────────────────────────
# route_from_tool_node
# ──────────────────────────────────────────────

def test_route_from_tool_node_sem_especialista_vai_para_secretaria():
    assert route_from_tool_node({"current_specialist": None}) == "agente_secretaria"


def test_route_from_tool_node_sem_chave_vai_para_secretaria():
    assert route_from_tool_node({}) == "agente_secretaria"


@pytest.mark.parametrize("specialist", [
    "agente_condominial",
    "agente_contratos",
    "agente_direito_consumidor",
])
def test_route_from_tool_node_com_especialista_roteia_de_volta(specialist):
    assert route_from_tool_node({"current_specialist": specialist}) == specialist
