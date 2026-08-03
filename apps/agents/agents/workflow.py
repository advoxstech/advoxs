import operator
from typing import Annotated

from langchain.messages import AnyMessage
from langgraph.graph import START, StateGraph
from typing_extensions import TypedDict

from agents.nodes import agent_node, tool_node


class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    attachments: list
    conversation_id: str
    num_before_messages: int
    current_agent_id: str | None
    receptive_message_specialist: bool
    agents: list[dict]
    # Documentos gerados nesta invocação (fazer_contrato/fazer_multa/etc, ver
    # agents/tools.py) — acumula por toda a conversa (mesmo reducer de
    # `messages`); call_agent.py fatia só os novos, mesmo padrão usado pra
    # `messages`.
    generated_documents: Annotated[list[dict], operator.add]


graph = StateGraph(State)

graph.add_node("agent_node", agent_node)
graph.add_node("tool_node", tool_node)

graph.add_edge(START, "agent_node")
graph.add_edge("tool_node", "agent_node")
