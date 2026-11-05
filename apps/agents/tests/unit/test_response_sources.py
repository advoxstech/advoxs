from uuid import uuid4

import pytest
from langgraph.graph import END

from agents.nodes import agent_node, tool_node
from agents.response_sources import collect_search, response_evidence
from tests.factories import ai_with_tool_call, base_state, mock_model

DOC = str(uuid4())


def evidence_state():
    state = base_state()
    state["conversation_id"] = "tenant-a:contact"
    state["current_agent_id"] = state["agents"][0]["id"]
    agent = state["agents"][0]
    agent["knowledge_base_file_ids"] = [DOC]
    return state, agent


def hit(**metadata):
    return {
        "chunk_id": "chunk-1",
        "text": "A consulta dura 45 minutos.",
        "metadata": {
            "tenant_id": "tenant-a",
            "conversation_id": "kb",
            "doc_id": DOC,
            "name": "Atendimento.pdf",
            **metadata,
        },
    }


def test_only_authorized_hits_are_exposed_and_referenced():
    state, agent = evidence_state()
    result, updates = collect_search(
        {
            "status": "found",
            "results": [
                hit(),
                hit(tenant_id="other"),
                hit(conversation_id="contact"),
                hit(doc_id=str(uuid4())),
            ],
        },
        state,
        agent,
    )
    assert len(result["results"]) == 1
    ref = result["results"][0]["reference_id"]
    state.update(updates)
    assert response_evidence(state, agent)["status"] == "not_used"
    evidence = response_evidence(state, agent, [ref, ref, "invented"])
    assert evidence["status"] == "referenced"
    assert len(evidence["sources"]) == 1
    assert evidence["sources"][0]["page"] is None
    assert evidence["sources"][0]["excerpt"] == hit()["text"]
    assert "agent_id" not in evidence["sources"][0]
    other = {**agent, "id": "other-agent"}
    assert response_evidence(state, other, [ref])["sources"] == []
    assert response_evidence({"source_candidates": {}}, agent, [ref])["sources"] == []


def test_page_range_comes_from_the_indexed_chunk():
    state, agent = evidence_state()
    result, _ = collect_search(
        {"status": "found", "results": [hit(page_start=12, page_end=13)]}, state, agent
    )
    source = result["results"][0]
    assert (source["page"], source["page_end"]) == (12, 13)


def test_invalid_pages_are_dropped():
    state, agent = evidence_state()
    result, _ = collect_search(
        {"status": "found", "results": [hit(page_start="3", page_end=1)]}, state, agent
    )
    source = result["results"][0]
    assert (source["page"], source["page_end"]) == (None, None)


def test_partial_reading_is_explained_to_the_agent():
    state, agent = evidence_state()
    result, _ = collect_search(
        {
            "status": "found",
            "results": [hit(page_start=1, page_end=1, partial_pages=[7, 3, "x"])],
            "guidance": "Priorize os trechos relevantes.",
        },
        state,
        agent,
    )
    assert result["results"][0]["partial_pages"] == [3, 7]
    assert result["guidance"].startswith("Priorize os trechos relevantes.")
    assert "Atendimento.pdf: páginas 3, 7" in result["guidance"]
    assert "Não invente" in result["guidance"]


def test_complete_reading_has_no_partial_warning():
    state, agent = evidence_state()
    result, _ = collect_search({"status": "found", "results": [hit()]}, state, agent)
    assert result["results"][0]["partial_pages"] == []
    assert "Leitura parcial" not in result["guidance"]


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([], "not_searched"),
        (["empty"], "empty"),
        (["found"], "not_used"),
        (["empty", "unavailable"], "unavailable"),
    ],
)
def test_query_outcomes_are_distinct(events, expected):
    state, agent = evidence_state()
    state["source_searches"] = {agent["id"]: events}
    assert response_evidence(state, agent)["status"] == expected


def test_no_documents_and_invalid_results_are_not_empty_searches():
    state, agent = evidence_state()
    assert (
        response_evidence(state, {**agent, "knowledge_base_file_ids": []})["status"]
        == "no_documents"
    )
    result, _ = collect_search(
        {"status": "found", "results": [hit(tenant_id="other")]}, state, agent
    )
    assert result["status"] == "unavailable"


async def test_structured_final_is_clean_and_keeps_tokens(monkeypatch):
    state, agent = evidence_state()
    result, updates = collect_search(
        {"status": "found", "results": [hit(page_start=3, page_end=3)]}, state, agent
    )
    state.update(updates)
    ref = result["results"][0]["reference_id"]
    reply = ai_with_tool_call(
        "responder_com_fontes",
        {
            "answer": "A consulta dura 45 minutos.",
            "reference_ids": [ref, "invented"],
        },
    )
    reply.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    monkeypatch.setattr("agents.nodes.model", mock_model(reply))
    result = await agent_node(state)
    message = result.update["messages"][0]
    assert result.goto == END
    assert message.content == "A consulta dura 45 minutos."
    assert message.tool_calls == []
    assert message.usage_metadata["total_tokens"] == 15
    assert message.additional_kwargs["response_sources"]["sources"][0]["page"] == 3


@pytest.fixture
def final_logs():
    from loguru import logger

    lines = []
    sink = logger.add(lambda m: lines.append(m.record["message"]), level="INFO")
    yield lambda: [line for line in lines if line.startswith("Resposta final")]
    logger.remove(sink)


async def test_final_log_counts_sent_and_valid_ids(monkeypatch, final_logs):
    state, agent = evidence_state()
    result, updates = collect_search({"status": "found", "results": [hit()]}, state, agent)
    state.update(updates)
    ref = result["results"][0]["reference_id"]
    reply = ai_with_tool_call(
        "responder_com_fontes",
        {"answer": "A consulta dura 45 minutos.", "reference_ids": [ref, "invented"]},
    )
    monkeypatch.setattr("agents.nodes.model", mock_model(reply))
    await agent_node(state)
    assert final_logs() == [
        "Resposta final | via_ferramenta=sim | ids_enviados=2 | ids_validos=1 | status=referenced"
    ]


async def test_final_log_when_tool_is_used_without_ids(monkeypatch, final_logs):
    state, agent = evidence_state()
    _, updates = collect_search({"status": "found", "results": [hit()]}, state, agent)
    state.update(updates)
    reply = ai_with_tool_call(
        "responder_com_fontes", {"answer": "A consulta dura 45 minutos.", "reference_ids": []}
    )
    monkeypatch.setattr("agents.nodes.model", mock_model(reply))
    await agent_node(state)
    assert final_logs() == [
        "Resposta final | via_ferramenta=sim | ids_enviados=0 | ids_validos=0 | status=not_used"
    ]


async def test_final_log_when_tool_is_ignored(monkeypatch, final_logs):
    from langchain_core.messages import AIMessage

    state, agent = evidence_state()
    _, updates = collect_search({"status": "found", "results": [hit()]}, state, agent)
    state.update(updates)
    monkeypatch.setattr(
        "agents.nodes.model", mock_model(AIMessage(content="A consulta dura 45 minutos."))
    )
    await agent_node(state)
    assert final_logs() == [
        "Resposta final | via_ferramenta=nao | ids_enviados=0 | ids_validos=0 | status=not_used"
    ]


async def test_no_final_log_while_calling_other_tools(monkeypatch, final_logs):
    state, _ = evidence_state()
    reply = ai_with_tool_call("buscar_base_conhecimento_agente", {"query": "duração"})
    monkeypatch.setattr("agents.nodes.model", mock_model(reply))
    await agent_node(state)
    assert final_logs() == []


async def test_final_cannot_be_combined_with_a_transfer(monkeypatch):
    state, _ = evidence_state()
    reply = ai_with_tool_call(
        "responder_com_fontes",
        {
            "answer": "Não enviar ainda",
            "reference_ids": [],
        },
    )
    reply.tool_calls.append(
        {"name": "transfer_to_agent", "args": {"agent_id": "other-1"}, "id": "transfer"}
    )
    monkeypatch.setattr("agents.nodes.model", mock_model(reply))
    result = await agent_node(state)
    assert result.goto == "tool_node"
    assert result.update["messages"][0].content == ""
    state["messages"] = result.update["messages"]
    update = await tool_node(state)
    assert len(update["messages"]) == 2
    assert "sozinha" in update["messages"][0].content


def test_partial_search_failure_is_preserved_with_valid_sources():
    state, agent = evidence_state()
    result, updates = collect_search({"status": "found", "results": [hit()]}, state, agent)
    state.update(updates)
    _, updates = collect_search({"status": "unavailable", "results": []}, state, agent)
    state.update(updates)
    evidence = response_evidence(state, agent, [result["results"][0]["reference_id"]])
    assert evidence["status"] == "referenced"
    assert evidence["search_failed"]


async def test_graph_search_finalization_and_next_turn_without_old_evidence(monkeypatch):
    import ast
    from unittest.mock import AsyncMock, MagicMock

    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from agents.workflow import graph

    state, agent = evidence_state()
    retrieval = AsyncMock(return_value=[hit()])
    monkeypatch.setattr("agents.tools.retrieval_escritorio", retrieval)
    turn = 1
    old_ref = None

    async def answer(messages):
        nonlocal old_ref
        if messages[-1].type == "tool":
            observation = ast.literal_eval(messages[-1].content)
            old_ref = observation["results"][0]["reference_id"]
            return ai_with_tool_call(
                "responder_com_fontes",
                {
                    "answer": "Consulta de 45 minutos",
                    "reference_ids": [old_ref],
                },
            )
        if turn == 1:
            return ai_with_tool_call(
                "buscar_base_conhecimento_agente",
                {
                    "query": "duração",
                    "conversation_id": "forged:other",
                    "knowledge_base_file_ids": ["forged-document"],
                },
            )
        return ai_with_tool_call(
            "responder_com_fontes",
            {
                "answer": "Outra resposta",
                "reference_ids": [old_ref],
            },
        )

    model = MagicMock()
    model.bind_tools.return_value.ainvoke = AsyncMock(side_effect=answer)
    monkeypatch.setattr("agents.nodes.model", model)
    compiled = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "tenant-a:test"}}
    result = await compiled.ainvoke(
        {**state, "source_candidates": {}, "source_searches": {}}, config
    )
    final = result["messages"][-1]
    assert final.content == "Consulta de 45 minutos"
    assert final.tool_calls == []
    assert final.additional_kwargs["response_sources"]["sources"][0]["document_id"] == DOC
    retrieval.assert_awaited_once_with("tenant-a:contact", "duração", doc_ids=[DOC])
    turn = 2
    result = await compiled.ainvoke(
        {
            "messages": [HumanMessage(content="outra pergunta")],
            "source_candidates": {},
            "source_searches": {},
        },
        config,
    )
    evidence = result["messages"][-1].additional_kwargs["response_sources"]
    assert evidence["sources"] == []
    assert evidence["status"] == "not_searched"
