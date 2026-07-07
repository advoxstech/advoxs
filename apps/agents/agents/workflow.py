from langgraph.graph import StateGraph, START, END
from typing_extensions import Annotated, TypedDict
from langchain.messages import AnyMessage
from agents.nodes import *
from agents.helpers import *
import operator
from loguru import logger


class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    attachments: list
    conversation_id: str
    num_before_messages: int
    current_specialist: str | None = None
    receptive_message_specialist: bool = False

graph = StateGraph(State)

graph.add_node("agente_secretaria", agente_secretaria)

graph.add_node("agente_condominial", agente_condominial)
graph.add_node("agente_contratos", agente_contratos)
graph.add_node("agente_direito_consumidor", agente_direito_consumidor)

graph.add_node("tool_node", tool_node)


def route_from_start(state: dict) -> str:
    current = state.get("current_specialist")
    if current is None:
        logger.info("Roteando para agente_secretaria")
        return "agente_secretaria"
    logger.info("Roteando para especialista | specialist={}", current)
    return current


graph.add_conditional_edges(START, route_from_start, ["agente_secretaria", "agente_condominial", "agente_contratos", "agente_direito_consumidor"])


def route_from_tool_node(state: dict) -> str:  
    current = state.get("current_specialist")
    return current if current else "agente_secretaria"

graph.add_conditional_edges("tool_node", route_from_tool_node, [
    "agente_secretaria", "agente_condominial", "agente_contratos", "agente_direito_consumidor"
])
