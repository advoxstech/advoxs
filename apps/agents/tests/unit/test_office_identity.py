from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agents.nodes import agent_node, tool_node
from agents.office_identity import (
    document_acknowledgement,
    generated_this_turn,
    identity_rule,
    requested_document_tool,
)
from tests.factories import ai_response, ai_with_tool_call, base_state, mock_model


def test_identity_uses_current_office_name_without_inventing_one():
    rule = identity_rule({"office_name": "Helder Goes Advocacia"})
    assert '"Helder Goes Advocacia"' in rule
    assert "prevalece" in rule
    assert "histórico" in rule
    assert "GVA" not in identity_rule({})


async def test_current_office_identity_precedes_legacy_prompt_and_history(monkeypatch):
    monkeypatch.setenv("AGENTS_AUTO_SEARCH", "false")
    state = base_state(
        messages=[AIMessage(content="Sou Sofia da GVA"), HumanMessage(content="Quem é você?")]
    )
    state["agents"][0].update(
        office_name="Helder Goes Advocacia", instructions="Exemplo antigo GVA"
    )
    model = mock_model(ai_response("Sou assistente digital da Helder Goes Advocacia."))
    with patch("agents.nodes.model", model):
        result = await agent_node(state)
    prompt = model.bind_tools.return_value.ainvoke.await_args.args[0][0].content
    assert prompt.index("Helder Goes Advocacia") < prompt.index("Exemplo antigo GVA")
    assert "Helder Goes" in result.update["messages"][0].content


def test_acknowledgement_does_not_ask_for_authorization_or_offer_channels():
    for filenames in (["Aviso.pdf"], ["Aviso.pdf", "Contrato.pdf"]):
        answer = document_acknowledgement(filenames)
        assert "automaticamente" in answer
        assert "?" not in answer
        assert "e-mail" not in answer
        assert "WhatsApp" not in answer


def test_generated_file_context_does_not_leak_between_turns():
    success = ToolMessage(
        content="PDF gerado",
        tool_call_id="doc",
        name="enviar_aviso",
        additional_kwargs={"generated_filenames": ["Aviso.pdf"]},
    )
    assert generated_this_turn([HumanMessage(content="Gere o aviso"), success]) == ["Aviso.pdf"]
    assert generated_this_turn([success, HumanMessage(content="Gere outro PDF")]) == []


async def test_success_tool_result_explains_automatic_attachment(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from langgraph.types import Command

    fake = MagicMock(name="enviar_aviso")
    fake.name = "enviar_aviso"
    fake.ainvoke = AsyncMock(
        return_value=Command(
            update={
                "generated_documents": [
                    {"filename": "Aviso.pdf", "link": "https://example.test/pdf"}
                ]
            }
        )
    )
    monkeypatch.setattr("agents.nodes.tools", [fake])
    state = base_state(messages=[ai_with_tool_call("enviar_aviso", {})])
    result = await tool_node(state)
    tool_message = result["messages"][0]
    assert "automaticamente" in tool_message.content
    assert tool_message.additional_kwargs["generated_filenames"] == ["Aviso.pdf"]


async def test_failed_generation_never_claims_attachment(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    fake = MagicMock()
    fake.name = "enviar_aviso"
    fake.ainvoke = AsyncMock(return_value="Falha ao gerar o documento")
    monkeypatch.setattr("agents.nodes.tools", [fake])
    result = await tool_node(base_state(messages=[ai_with_tool_call("enviar_aviso", {})]))
    assert "Falha" in result["messages"][0].content
    assert not result["messages"][0].additional_kwargs


async def test_final_redundant_consent_becomes_truthful_generation_acknowledgement(monkeypatch):
    monkeypatch.setenv("AGENTS_AUTO_SEARCH", "false")
    messages = [
        HumanMessage(content="Gere o aviso"),
        ToolMessage(
            content="PDF gerado",
            tool_call_id="doc",
            name="enviar_aviso",
            additional_kwargs={"generated_filenames": ["Aviso.pdf"]},
        ),
    ]
    model = mock_model(ai_response("Posso enviar o PDF?"))
    with patch("agents.nodes.model", model):
        result = await agent_node(base_state(messages=messages))
    answer = result.update["messages"][0].content
    assert "gerado com sucesso" in answer
    assert "automaticamente" in answer
    assert "?" not in answer
    model.bind_tools.assert_not_called()


@pytest.mark.parametrize(
    "user_request,expected",
    [
        ("Gere um aviso em PDF para a reunião", "enviar_aviso"),
        ("Preciso gerar um comunicado informativo", "enviar_aviso"),
        ("Elabore um contrato de prestação de serviços", "fazer_contrato"),
        ("Faça uma advertência", "fazer_advertencia"),
        ("Não gere uma multa, gere um comunicado", "enviar_aviso"),
        ("Gere um contrato e faça um aviso", None),
        ("Se eu pedir para gerar uma advertência, como funciona?", None),
        ("Ele disse: gere uma advertência", None),
    ],
)
def test_explicit_document_type(user_request, expected):
    assert requested_document_tool([HumanMessage(content=user_request)]) == expected


async def test_notice_cannot_be_generated_as_a_warning(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    warning = MagicMock()
    warning.name = "fazer_advertencia"
    warning.ainvoke = AsyncMock()
    monkeypatch.setattr("agents.nodes.tools", [warning])
    result = await tool_node(
        base_state(
            messages=[
                HumanMessage(content="Gere um comunicado informativo sobre reunião"),
                ai_with_tool_call("fazer_advertencia", {}),
            ]
        )
    )
    warning.ainvoke.assert_not_awaited()
    assert "enviar_aviso" in result["messages"][0].content
    assert "generated_documents" not in result


async def test_graph_rejects_wrong_type_then_attaches_without_redundant_consent(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from langgraph.types import Command

    from agents.workflow import graph

    monkeypatch.setenv("AGENTS_AUTO_SEARCH", "false")
    warning, notice = MagicMock(), MagicMock()
    warning.name, notice.name = "fazer_advertencia", "enviar_aviso"
    warning.ainvoke = AsyncMock()
    notice.ainvoke = AsyncMock(
        return_value=Command(
            update={
                "generated_documents": [
                    {"filename": "Aviso.pdf", "link": "https://example.test/pdf"}
                ]
            }
        )
    )
    monkeypatch.setattr("agents.nodes.tools", [warning, notice])
    model = mock_model(ai_response("unused"))
    model.bind_tools.return_value.ainvoke.side_effect = [
        ai_with_tool_call("fazer_advertencia", {}),
        ai_with_tool_call("enviar_aviso", {}),
        ai_response("Posso enviar o PDF nesta conversa?"),
    ]
    monkeypatch.setattr("agents.nodes.model", model)
    result = await graph.compile().ainvoke(
        base_state(
            current_agent_id="other-1",
            messages=[HumanMessage(content="Gere um comunicado informativo")],
        )
    )
    warning.ainvoke.assert_not_awaited()
    notice.ainvoke.assert_awaited_once()
    assert result["generated_documents"][0]["filename"] == "Aviso.pdf"
    assert result["messages"][-1].content.startswith("PDF gerado com sucesso")
    assert "?" not in result["messages"][-1].content
    assert model.bind_tools.return_value.ainvoke.await_count == 2
