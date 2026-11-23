from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agents.legal_safety import REVIEW_ANSWER, legal_review_reason
from agents.nodes import agent_node, tool_node
from tests.factories import ai_response, ai_with_tool_call, base_state, mock_model


@pytest.mark.parametrize(
    "answer",
    [
        "O prazo legal é de sete dias.",
        "A empresa deve reembolsar o frete.",
        "O CDC, art. 49, garante esse direito.",
        "Você tem direito a indenização.",
    ],
)
def test_normative_conclusions_require_legal_evidence(answer):
    assert legal_review_reason(answer, {"sources": []})
    # A company policy or a contract is not a legal reference.
    evidence = {"sources": [{"document_id": "policy"}]}
    assert legal_review_reason(answer, evidence, ["law"])
    evidence["sources"] = [{"document_id": "law"}]
    assert legal_review_reason(answer, evidence, ["law"]) is None
    evidence["search_failed"] = True
    assert legal_review_reason(answer, evidence, ["law"])


@pytest.mark.parametrize(
    "answer",
    [
        "O contrato informa carência de 73 dias, mensalidade R$ 2.470 e aviso de 45 dias.",
        "Qual a data de recebimento do produto?",
        "Não encontrei o foro no documento enviado.",
    ],
)
def test_documentary_facts_and_intake_are_not_legal_conclusions(answer):
    assert legal_review_reason(answer, {"sources": []}) is None


def test_known_withdrawal_regression_is_blocked_even_with_sources():
    assert legal_review_reason(
        "O frete depende da política da loja.",
        {"sources": [{"document_id": "law"}]},
        ["law"],
        "Compra online: arrependimento, quem paga o frete?",
    )


async def test_unsubstantiated_final_is_replaced_and_review_is_registered(monkeypatch):
    monkeypatch.setenv("AGENTS_AUTO_SEARCH", "false")
    with patch("agents.nodes.model", mock_model(ai_response("O prazo legal é de sete dias."))):
        result = await agent_node(base_state(current_agent_id="other-1"))
    assert result.update["messages"][0].content == REVIEW_ANSWER
    assert result.update["human_request_pending"]
    assert result.update["human_request_agent_id"] == "other-1"
    assert "state" not in result.update  # registering does not pause automation


async def test_pending_request_pins_specialist_and_does_not_repeat_event(monkeypatch):
    monkeypatch.setenv("AGENTS_AUTO_SEARCH", "false")
    with patch("agents.nodes.model", mock_model(ai_response("O CDC exige isso."))):
        result = await agent_node(
            base_state(
                current_agent_id="entry-1",
                human_request_agent_id="other-1",
                human_request_pending=True,
            )
        )
    assert result.update["current_agent_id"] == "other-1"
    assert "human_requests" not in result.update


async def test_request_uses_trusted_agent_and_blocks_parallel_transfer_and_document():
    response = AIMessage(
        content="",
        tool_calls=[
            {"name": "transfer_to_agent", "args": {"agent_id": "entry-1"}, "id": "transfer"},
            {
                "name": "solicitar_atendimento_humano",
                "args": {"motivo": "Cliente quer advogado", "requesting_agent_id": "forged"},
                "id": "human",
            },
            {"name": "fazer_contrato", "args": {}, "id": "doc"},
        ],
    )
    result = await tool_node(base_state(current_agent_id="other-1", messages=[response]))
    assert "current_agent_id" not in result
    assert result["human_request_agent_id"] == "other-1"
    assert result["human_requests"] == [{"reason": "Cliente quer advogado", "agent_id": "other-1"}]
    assert all(isinstance(message, ToolMessage) for message in result["messages"])
    assert len(result["messages"]) == 3


async def test_pending_request_blocks_digital_transfer_without_farewell():
    response = ai_with_tool_call("transfer_to_agent", {"agent_id": "entry-1"})
    result = await tool_node(
        base_state(
            current_agent_id="other-1",
            human_request_pending=True,
            messages=[response],
        )
    )
    assert "current_agent_id" not in result
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], ToolMessage)


async def test_transfer_confirmation_precedes_specialist_reply(monkeypatch):
    monkeypatch.setenv("AGENTS_AUTO_SEARCH", "false")
    response = ai_with_tool_call("transfer_to_agent", {"agent_id": "other-1"})
    state = base_state(current_agent_id="entry-1", messages=[HumanMessage(content="Meu caso")])
    with patch("agents.nodes.model", mock_model(response)):
        first = await agent_node(state)
    state["messages"] += first.update["messages"]
    state.update({k: v for k, v in first.update.items() if k != "messages"})
    transferred = await tool_node(state)
    state["messages"] += transferred.pop("messages")
    state.update(transferred)
    state["agents"][1]["knowledge_base_file_ids"] = []
    with patch(
        "agents.nodes.model", mock_model(ai_response("Sou o assistente digital desta área."))
    ):
        last = await agent_node(state)
    answers = [
        m.content
        for m in state["messages"] + last.update["messages"]
        if isinstance(m, AIMessage) and m.content
    ]
    assert len(answers) == 2
    assert "encaminhar" in answers[0]
    assert "assistente digital desta área" in answers[1]
