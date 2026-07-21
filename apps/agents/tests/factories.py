from langchain_core.messages import AIMessage, HumanMessage
from unittest.mock import AsyncMock, MagicMock


def ai_with_tool_call(tool_name: str, args: dict, content: str = "") -> AIMessage:
    return AIMessage(
        content=content,
        tool_calls=[{
            "id": "call_test_123",
            "name": tool_name,
            "args": args,
            "type": "tool_call",
        }],
    )


def ai_response(content: str) -> AIMessage:
    return AIMessage(content=content)


def mock_model(return_value: AIMessage) -> MagicMock:
    mock_bound = MagicMock()
    mock_bound.ainvoke = AsyncMock(return_value=return_value)
    model = MagicMock()
    model.bind_tools.return_value = mock_bound
    return model


def base_state(**overrides) -> dict:
    state = {
        "messages": [HumanMessage(content="mensagem de teste")],
        "num_before_messages": 10,
        "attachments": [],
        "conversation_id": "conv-test",
        "current_agent_id": None,
        "receptive_message_specialist": False,
        "agents": [
            {
                "id": "entry-1",
                "name": "Secretária",
                "instructions": "Você é a secretária de triagem.",
                "is_entry_point": True,
                "knowledge_base_file_ids": [],
            },
            {
                "id": "other-1",
                "name": "Condominial",
                "instructions": "Você é o especialista condominial.",
                "is_entry_point": False,
                "knowledge_base_file_ids": ["kb-1"],
            },
        ],
    }
    state.update(overrides)
    return state
