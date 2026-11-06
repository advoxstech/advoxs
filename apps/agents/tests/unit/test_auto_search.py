import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import agents.tools as tools_module
from agents import auto_search
from tests.factories import ai_response, base_state, mock_model

NOTA = "[Documento recebido e processado: anexo-1.pdf — disponível para busca.]"


def _resposta(conteudo: str) -> AIMessage:
    """Resposta do grafo (o agent_node sempre grava response_sources)."""
    return AIMessage(content=conteudo, additional_kwargs={"response_sources": {}})


def _especialista():
    return base_state()["agents"][1]  # other-1, com arquivo kb-1 na base


def _recepcao():
    return base_state()["agents"][0]  # entry-1, sem arquivos


@pytest.fixture(autouse=True)
def _com_busca_automatica(monkeypatch):
    monkeypatch.setenv("AGENTS_AUTO_SEARCH", "true")


# ──────────────────────────────────────────────
# plan / turn_query
# ──────────────────────────────────────────────


def test_pergunta_com_documento_busca_nas_duas_bases_com_o_texto_do_cliente():
    mensagens = [HumanMessage(content=f"qual a multa do contrato Zeta-177?\n{NOTA}")]

    calls = auto_search.plan(mensagens, _especialista())

    assert [c["name"] for c in calls] == [auto_search.CONTACT_TOOL, auto_search.KB_TOOL]
    assert all(c["args"] == {"query": "qual a multa do contrato Zeta-177?"} for c in calls)
    assert all(c["id"].startswith(auto_search.AUTO_SEARCH_ID_PREFIX) for c in calls)


@pytest.mark.parametrize("texto", ["ok", "obrigado!", "bom dia", "fui contratado"])
def test_mensagem_trivial_nao_busca(texto):
    mensagens = [HumanMessage(content=f"{NOTA}"), _resposta("oi"), HumanMessage(content=texto)]

    assert auto_search.plan(mensagens, _especialista()) == []


@pytest.mark.parametrize("texto", ["prazo?", "e o 123", "qual o foro eleito nesse contrato"])
def test_pergunta_curta_com_interrogacao_ou_numero_busca(texto):
    assert auto_search.plan([HumanMessage(content=texto)], _especialista())


def test_sem_documento_do_cliente_e_sem_base_nao_busca():
    assert auto_search.plan([HumanMessage(content="qual a multa?")], _recepcao()) == []


def test_sem_documento_busca_so_na_base_do_agente():
    calls = auto_search.plan([HumanMessage(content="qual a multa?")], _especialista())

    assert [c["name"] for c in calls] == [auto_search.KB_TOOL]


def test_documento_enviado_em_turno_anterior_continua_valendo():
    mensagens = [
        HumanMessage(content=f"segue o contrato\n{NOTA}"),
        _resposta("recebi"),
        HumanMessage(content="qual o valor do aluguel?"),
    ]

    calls = auto_search.plan(mensagens, _recepcao())

    assert [c["name"] for c in calls] == [auto_search.CONTACT_TOOL]
    assert calls[0]["args"]["query"] == "qual o valor do aluguel?"


def test_texto_da_busca_junta_mensagens_desde_a_ultima_resposta():
    mensagens = [
        HumanMessage(content="pergunta antiga?"),
        _resposta("resposta antiga"),
        HumanMessage(content=f"qual a multa?\n{NOTA}"),
        # Aviso do atendente sincronizado no checkpoint: não é resposta do agente.
        AIMessage(content="Recebi seu documento. Ele é grande…"),
        HumanMessage(content="e o foro? [A leitura do documento demorou e o cliente…]"),
    ]

    assert auto_search.turn_query(mensagens) == "qual a multa? e o foro?"


def test_palavras_incertas_da_transcricao_ficam_no_texto_da_busca():
    mensagens = [
        HumanMessage(content="[Áudio do cliente, transcrito automaticamente]: o contrato [?Zeta?]")
    ]

    assert auto_search.turn_query(mensagens) == ": o contrato [?Zeta?]"


def test_busca_ja_feita_no_turno_nao_se_repete():
    mensagens = [
        HumanMessage(content=f"qual a multa?\n{NOTA}"),
        ToolMessage(content="{}", tool_call_id="a", name=auto_search.CONTACT_TOOL),
        ToolMessage(content="{}", tool_call_id="b", name=auto_search.KB_TOOL),
    ]

    assert auto_search.plan(mensagens, _especialista()) == []


def test_depois_da_transferencia_o_especialista_busca_na_base_dele_mas_nao_repete_o_documento():
    mensagens = [
        HumanMessage(content=f"qual a multa?\n{NOTA}"),
        ToolMessage(content="{}", tool_call_id="a", name=auto_search.CONTACT_TOOL),
        ToolMessage(content="{}", tool_call_id="b", name=auto_search.KB_TOOL),
        ToolMessage(content="", tool_call_id="c", name="transfer_to_agent"),
    ]

    calls = auto_search.plan(mensagens, _especialista())

    assert [c["name"] for c in calls] == [auto_search.KB_TOOL]


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(None, True), ("true", True), ("1", True), ("false", False), ("0", False), ("OFF", False)],
)
def test_flag_de_ambiente(monkeypatch, valor, esperado):
    if valor is None:
        monkeypatch.delenv("AGENTS_AUTO_SEARCH", raising=False)
    else:
        monkeypatch.setenv("AGENTS_AUTO_SEARCH", valor)

    assert auto_search.enabled() is esperado


# ──────────────────────────────────────────────
# drop_previous_turns
# ──────────────────────────────────────────────


def test_buscas_automaticas_de_turnos_anteriores_saem_do_contexto():
    antiga = auto_search.search_message(
        [{"id": "auto-search-1", "name": auto_search.KB_TOOL, "args": {}, "type": "tool_call"}]
    )
    atual = auto_search.search_message(
        [{"id": "auto-search-2", "name": auto_search.KB_TOOL, "args": {}, "type": "tool_call"}]
    )
    busca_do_modelo = AIMessage(
        content="", tool_calls=[{"id": "call-9", "name": "x", "args": {}, "type": "tool_call"}]
    )
    mensagens = [
        HumanMessage(content="pergunta 1?"),
        antiga,
        ToolMessage(content="trechos antigos", tool_call_id="auto-search-1"),
        busca_do_modelo,
        ToolMessage(content="busca do modelo", tool_call_id="call-9"),
        _resposta("resposta 1"),
        HumanMessage(content="pergunta 2?"),
        atual,
        ToolMessage(content="trechos novos", tool_call_id="auto-search-2"),
    ]

    resultado = auto_search.drop_previous_turns(mensagens)

    assert antiga not in resultado
    assert "trechos antigos" not in [m.content for m in resultado]
    assert busca_do_modelo in resultado
    assert atual in resultado
    assert "trechos novos" in [m.content for m in resultado]


# ──────────────────────────────────────────────
# agent_node / tool_node
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_node_busca_antes_de_chamar_o_modelo(monkeypatch):
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)
    state = base_state(
        messages=[HumanMessage(content=f"qual a multa?\n{NOTA}")],
        current_agent_id="other-1",
        receptive_message_specialist=True,
    )

    result = await agent_node(state)

    model.bind_tools.assert_not_called()
    assert result.goto == "tool_node"
    busca = result.update["messages"][0]
    assert busca.content == ""
    assert [c["name"] for c in busca.tool_calls] == [
        auto_search.CONTACT_TOOL,
        auto_search.KB_TOOL,
    ]
    # A primeira resposta (apresentação + parecer) é a chamada seguinte.
    assert "receptive_message_specialist" not in result.update


@pytest.mark.asyncio
async def test_agent_node_com_buscas_feitas_responde_com_a_regra_da_busca_automatica(monkeypatch):
    from agents.nodes import agent_node

    model = mock_model(ai_response("A multa é de 9%."))
    monkeypatch.setattr("agents.nodes.model", model)
    state = base_state(
        messages=[
            HumanMessage(content=f"qual a multa?\n{NOTA}"),
            ToolMessage(content="{}", tool_call_id="a", name=auto_search.CONTACT_TOOL),
            ToolMessage(content="{}", tool_call_id="b", name=auto_search.KB_TOOL),
        ],
        current_agent_id="other-1",
        receptive_message_specialist=True,
    )

    result = await agent_node(state)

    assert "tool_choice" not in model.bind_tools.call_args.kwargs
    prompt = model.bind_tools.return_value.ainvoke.call_args.args[0][0].content
    assert "Busca automática" in prompt
    assert result.update.get("receptive_message_specialist") is False


@pytest.mark.asyncio
async def test_agent_node_mensagem_trivial_vai_direto_ao_modelo(monkeypatch):
    from agents.nodes import agent_node

    model = mock_model(ai_response("de nada!"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agent_node(base_state(messages=[HumanMessage(content="obrigado")]))

    model.bind_tools.assert_called_once()


@pytest.mark.asyncio
async def test_tool_node_roda_as_duas_buscas_ao_mesmo_tempo(monkeypatch):
    from agents.nodes import tool_node

    async def devagar(*_args, **_kwargs):
        await asyncio.sleep(0.3)
        return [{"text": "trecho"}]

    monkeypatch.setattr(tools_module, "retrieval_usuario", AsyncMock(side_effect=devagar))
    monkeypatch.setattr(tools_module, "retrieval_escritorio", AsyncMock(side_effect=devagar))
    calls = auto_search.plan([HumanMessage(content=f"qual a multa?\n{NOTA}")], _especialista())
    state = base_state(
        messages=[
            HumanMessage(content=f"qual a multa?\n{NOTA}"),
            auto_search.search_message(calls),
        ],
        current_agent_id="other-1",
    )

    inicio = time.perf_counter()
    result = await tool_node(state)
    duracao = time.perf_counter() - inicio

    assert duracao < 0.5
    assert [m.name for m in result["messages"]] == [auto_search.CONTACT_TOOL, auto_search.KB_TOOL]
    assert [m.tool_call_id for m in result["messages"]] == [c["id"] for c in calls]
