from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import services.call_agent as module
from services.call_agent import sum_usage_breakdown


@pytest.mark.parametrize("existing", [False, True])
async def test_selected_agent_only_seeds_new_test_without_changing_roles(monkeypatch, existing):
    from langchain_core.messages import HumanMessage

    checkpointer = MagicMock()
    checkpointer.setup = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=checkpointer)
    context.__aexit__ = AsyncMock(return_value=False)
    saver = MagicMock()
    saver.from_conn_string.return_value = context
    monkeypatch.setattr(module, "AsyncPostgresSaver", saver)
    graph = MagicMock()
    prior = [HumanMessage(content="previous")] if existing else []
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(values={"messages": prior}))
    graph.ainvoke = AsyncMock(return_value={"messages": prior, "current_agent_id": "specialist"})
    monkeypatch.setattr(module.graph, "compile", MagicMock(return_value=graph))
    configs = [
        {
            "id": "specialist",
            "name": "Especialista",
            "instructions": "Draft",
            "is_entry_point": False,
        }
    ]
    await module.run_agent(
        "teste", "tenant:rascunho-123", agents=configs, test_start_agent_id="specialist"
    )
    values = graph.ainvoke.await_args.args[0]
    assert values["agents"][0]["is_entry_point"] is False
    if existing:
        assert "current_agent_id" not in values
    else:
        assert values["current_agent_id"] == "specialist"


def _ai(usage: dict | None):
    return SimpleNamespace(type="ai", usage_metadata=usage, content="resposta")


def _human():
    return SimpleNamespace(type="human", content="pergunta")


def test_soma_input_output_e_total_das_mensagens_de_ia():
    messages = [
        _human(),
        _ai({"input_tokens": 70, "output_tokens": 30, "total_tokens": 100}),
        _ai({"input_tokens": 200, "output_tokens": 50, "total_tokens": 250}),
    ]

    assert sum_usage_breakdown(messages) == {
        "input_tokens": 270,
        "output_tokens": 80,
        "total_tokens": 350,
    }


def test_mensagem_de_ia_sem_usage_conta_zero():
    messages = [_ai(None), _ai({"input_tokens": 60, "output_tokens": 20, "total_tokens": 80})]

    assert sum_usage_breakdown(messages) == {
        "input_tokens": 60,
        "output_tokens": 20,
        "total_tokens": 80,
    }


def test_mensagem_sem_atributo_usage_metadata_nao_quebra():
    messages = [SimpleNamespace(type="ai", content="x"), _human()]

    assert sum_usage_breakdown(messages) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


def test_usage_sem_chaves_de_input_output_usa_zero():
    messages = [_ai({"total_tokens": 40})]

    assert sum_usage_breakdown(messages) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 40,
    }


async def test_each_answer_keeps_its_sources_and_next_turn_resets_search_state(monkeypatch):
    from langchain_core.messages import AIMessage

    old = AIMessage(
        content="Resposta anterior",
        additional_kwargs={
            "response_sources": {"status": "referenced", "sources": [{"excerpt": "antigo"}]}
        },
    )
    evidence = {"status": "empty", "sources": [], "search_failed": False}
    current = AIMessage(content="Nova resposta", additional_kwargs={"response_sources": evidence})
    graph = MagicMock()
    graph.aget_state = AsyncMock(
        return_value=SimpleNamespace(
            values={
                "messages": [old],
                "source_candidates": {"old-ref": {}},
                "source_searches": {"a": ["found"]},
            }
        )
    )
    graph.ainvoke = AsyncMock(return_value={"messages": [old, current], "current_agent_id": "a"})
    monkeypatch.setattr(module.graph, "compile", MagicMock(return_value=graph))
    pointer = MagicMock()
    pointer.setup = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=pointer)
    context.__aexit__ = AsyncMock(return_value=False)
    saver = MagicMock()
    saver.from_conn_string.return_value = context
    monkeypatch.setattr(module, "AsyncPostgresSaver", saver)
    answers, usage, *_ = await module.run_agent(
        "oi", "tenant:contact", agents=[{"id": "a", "name": "Ana"}]
    )
    assert answers == ["Nova resposta"]
    assert usage["response_sources"] == [evidence]
    args = graph.ainvoke.await_args.args[0]
    assert args["source_candidates"] == {}
    assert args["source_searches"] == {}
