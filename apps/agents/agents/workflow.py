from langgraph.graph import StateGraph, START, END
from typing_extensions import Annotated, TypedDict
from langchain.messages import AnyMessage
from agents.nodes import agent_node, tool_node
import operator


class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    attachments: list
    conversation_id: str
    num_before_messages: int
    current_agent_id: str | None
    receptive_message_specialist: bool
    end_customer_billing: dict | None
    agents: list[dict]


graph = StateGraph(State)

graph.add_node("agent_node", agent_node)
graph.add_node("tool_node", tool_node)

graph.add_edge(START, "agent_node")
graph.add_edge("tool_node", "agent_node")
