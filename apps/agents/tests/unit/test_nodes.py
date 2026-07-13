import pytest
from unittest.mock import AsyncMock, patch
from langchain_core.messages import AIMessage
from langgraph.graph import END
from tests.factories import ai_with_tool_call, ai_response, mock_model, base_state

import agents.tools as tools_module


# ──────────────────────────────────────────────
# agente_secretaria
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_secretaria_sem_tool_calls_vai_para_end():
    from agents.nodes import agente_secretaria

    with patch("agents.nodes.model", mock_model(ai_response("Olá, como posso ajudar?"))):
        result = await agente_secretaria(base_state())

    assert result.goto == END


@pytest.mark.asyncio
async def test_secretaria_com_tool_call_vai_para_tool_node():
    from agents.nodes import agente_secretaria
    fake = ai_with_tool_call("transfer_to_specialist", {"current_specialist": "agente_condominial"})

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agente_secretaria(base_state())

    assert result.goto == "tool_node"


@pytest.mark.asyncio
async def test_secretaria_transfer_sem_content_injeta_despedida():
    """Quando transfere sem content, deve injetar mensagem de despedida antes de ir ao tool_node."""
    from agents.nodes import agente_secretaria
    fake = ai_with_tool_call(
        "transfer_to_specialist",
        {"current_specialist": "agente_condominial"},
        content="",
    )

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agente_secretaria(base_state())

    ai_msg = result.update["messages"][0]
    assert ai_msg.content != "", "Despedida não foi injetada"
    assert "condominial" in ai_msg.content.lower() or "especialista" in ai_msg.content.lower()


@pytest.mark.asyncio
async def test_secretaria_transfer_com_content_nao_sobrescreve():
    """Se o modelo já gerou content, não deve sobrescrever com despedida."""
    from agents.nodes import agente_secretaria
    fake = ai_with_tool_call(
        "transfer_to_specialist",
        {"current_specialist": "agente_condominial"},
        content="Um momento, vou transferir você.",
    )

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agente_secretaria(base_state())

    ai_msg = result.update["messages"][0]
    assert ai_msg.content == "Um momento, vou transferir você."


@pytest.mark.asyncio
async def test_secretaria_tool_call_mantem_tool_calls_na_mensagem():
    """A mensagem de saída deve preservar os tool_calls para o tool_node processar."""
    from agents.nodes import agente_secretaria
    fake = ai_with_tool_call("transfer_to_specialist", {"current_specialist": "agente_contratos"})

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agente_secretaria(base_state())

    ai_msg = result.update["messages"][0]
    assert ai_msg.tool_calls, "tool_calls não foram preservados na mensagem"
    assert ai_msg.tool_calls[0]["name"] == "transfer_to_specialist"


@pytest.mark.asyncio
async def test_secretaria_bind_inclui_gerar_link_pagamento(monkeypatch) -> None:
    from agents.nodes import agente_secretaria

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agente_secretaria(base_state(end_customer_billing={"enabled": False, "balance": 0, "packages": []}))

    bound_tools = model.bind_tools.call_args.args[0]
    tool_names = {t.name for t in bound_tools}
    assert "gerar_link_pagamento_cliente" in tool_names


@pytest.mark.asyncio
async def test_secretaria_injeta_pacotes_no_prompt_quando_sem_saldo(monkeypatch) -> None:
    from agents.nodes import agente_secretaria

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": True,
            "balance": 0,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agente_secretaria(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" in prompt_arg.content
    assert "p-1" in prompt_arg.content


@pytest.mark.asyncio
async def test_secretaria_nao_injeta_pacotes_quando_billing_desabilitado(monkeypatch) -> None:
    from agents.nodes import agente_secretaria

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": False,
            "balance": 0,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agente_secretaria(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" not in prompt_arg.content
    assert "Pacotes disponíveis" not in prompt_arg.content


@pytest.mark.asyncio
async def test_secretaria_nao_injeta_pacotes_com_saldo_positivo(monkeypatch) -> None:
    from agents.nodes import agente_secretaria

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": True,
            "balance": 500,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agente_secretaria(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" not in prompt_arg.content


@pytest.mark.asyncio
async def test_secretaria_transfer_sem_content_pula_despedida_quando_bloqueado():
    """Quando a transferência vai ser bloqueada (sem saldo), não injeta despedida de
    transferência — o tool_node ainda vai rodar e transfer_to_specialist vai recusar,
    então a despedida ("vou te passar pro especialista agora") ficaria contraditória."""
    from agents.nodes import agente_secretaria
    fake = ai_with_tool_call(
        "transfer_to_specialist",
        {"current_specialist": "agente_condominial"},
        content="",
    )

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agente_secretaria(
            base_state(end_customer_billing={"enabled": True, "balance": 0, "packages": []})
        )

    ai_msg = result.update["messages"][0]
    assert ai_msg.content == ""
    assert ai_msg.tool_calls, "tool_calls devem ser preservados mesmo sem despedida"
    assert result.goto == "tool_node"


@pytest.mark.asyncio
async def test_secretaria_transfer_sem_content_injeta_despedida_quando_billing_desabilitado():
    """Sem billing habilitado (fluxo de escritório normal), a despedida continua sendo injetada."""
    from agents.nodes import agente_secretaria
    fake = ai_with_tool_call(
        "transfer_to_specialist",
        {"current_specialist": "agente_condominial"},
        content="",
    )

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agente_secretaria(
            base_state(end_customer_billing={"enabled": False, "balance": 0, "packages": []})
        )

    ai_msg = result.update["messages"][0]
    assert ai_msg.content != ""
    assert "condominial" in ai_msg.content.lower() or "especialista" in ai_msg.content.lower()


@pytest.mark.asyncio
async def test_secretaria_transfer_sem_content_injeta_despedida_quando_billing_com_saldo():
    """Com billing habilitado mas saldo positivo, a transferência não será bloqueada —
    a despedida deve continuar sendo injetada normalmente."""
    from agents.nodes import agente_secretaria
    fake = ai_with_tool_call(
        "transfer_to_specialist",
        {"current_specialist": "agente_condominial"},
        content="",
    )

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agente_secretaria(
            base_state(end_customer_billing={"enabled": True, "balance": 500, "packages": []})
        )

    ai_msg = result.update["messages"][0]
    assert ai_msg.content != ""
    assert "condominial" in ai_msg.content.lower() or "especialista" in ai_msg.content.lower()


# ──────────────────────────────────────────────
# agente_condominial
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_condominial_sem_tool_calls_sem_content_vai_para_end():
    from agents.nodes import agente_condominial

    with patch("agents.nodes.model", mock_model(ai_response(""))):
        result = await agente_condominial(base_state(receptive_message_specialist=False))

    assert result.goto == END


@pytest.mark.asyncio
async def test_condominial_com_tool_call_vai_para_tool_node():
    from agents.nodes import agente_condominial
    fake = ai_with_tool_call("transfer_to_specialist", {"current_specialist": "agente_contratos"})

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agente_condominial(base_state(receptive_message_specialist=False))

    assert result.goto == "tool_node"


@pytest.mark.asyncio
async def test_condominial_com_content_sem_tool_vai_para_end():
    from agents.nodes import agente_condominial

    with patch("agents.nodes.model", mock_model(ai_response("Vou te ajudar com o condomínio."))):
        result = await agente_condominial(base_state(receptive_message_specialist=False))

    assert result.goto == END


@pytest.mark.asyncio
async def test_condominial_first_run_reseta_flag():
    """receptive_message_specialist deve ser False no update após first_run=True."""
    from agents.nodes import agente_condominial

    with patch("agents.nodes.model", mock_model(ai_response("Olá! Sou o especialista condominial."))):
        result = await agente_condominial(base_state(receptive_message_specialist=True))

    assert result.update.get("receptive_message_specialist") is False


@pytest.mark.asyncio
async def test_condominial_nao_first_run_nao_inclui_flag_no_update():
    """Quando não é first_run, receptive_message_specialist não deve aparecer no update."""
    from agents.nodes import agente_condominial

    with patch("agents.nodes.model", mock_model(ai_response(""))):
        result = await agente_condominial(base_state(receptive_message_specialist=False))

    assert "receptive_message_specialist" not in result.update


@pytest.mark.asyncio
async def test_condominial_transfer_sem_content_injeta_despedida():
    from agents.nodes import agente_condominial
    fake = ai_with_tool_call(
        "transfer_to_specialist",
        {"current_specialist": "agente_contratos"},
        content="",
    )

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agente_condominial(base_state(receptive_message_specialist=False))

    ai_msg = result.update["messages"][0]
    assert ai_msg.content != ""
    assert "contratos" in ai_msg.content.lower() or "especialista" in ai_msg.content.lower()


# ──────────────────────────────────────────────
# agente_contratos
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_contratos_sem_tool_calls_vai_para_end():
    from agents.nodes import agente_contratos

    with patch("agents.nodes.model", mock_model(ai_response("Analisando seu contrato."))):
        result = await agente_contratos(base_state(receptive_message_specialist=False))

    assert result.goto == END


@pytest.mark.asyncio
async def test_contratos_com_tool_call_vai_para_tool_node():
    from agents.nodes import agente_contratos
    fake = ai_with_tool_call("transfer_to_specialist", {"current_specialist": "agente_condominial"})

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agente_contratos(base_state(receptive_message_specialist=False))

    assert result.goto == "tool_node"


@pytest.mark.asyncio
async def test_contratos_first_run_reseta_flag():
    from agents.nodes import agente_contratos

    with patch("agents.nodes.model", mock_model(ai_response("Sou especialista em contratos."))):
        result = await agente_contratos(base_state(receptive_message_specialist=True))

    assert result.update.get("receptive_message_specialist") is False


@pytest.mark.asyncio
async def test_contratos_nao_first_run_nao_inclui_flag_no_update():
    from agents.nodes import agente_contratos

    with patch("agents.nodes.model", mock_model(ai_response("Como posso ajudar?"))):
        result = await agente_contratos(base_state(receptive_message_specialist=False))

    assert "receptive_message_specialist" not in result.update


# ──────────────────────────────────────────────
# agente_direito_consumidor
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_direito_consumidor_sem_tool_calls_vai_para_end():
    from agents.nodes import agente_direito_consumidor

    with patch("agents.nodes.model", mock_model(ai_response("Vou orientar sobre seus direitos."))):
        result = await agente_direito_consumidor(base_state(receptive_message_specialist=False))

    assert result.goto == END


@pytest.mark.asyncio
async def test_direito_consumidor_com_tool_call_vai_para_tool_node():
    from agents.nodes import agente_direito_consumidor
    fake = ai_with_tool_call("bucar_base_conhecimento_usuario", {"query": "direito", "conversation_id": "conv-1"})

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agente_direito_consumidor(base_state(receptive_message_specialist=False))

    assert result.goto == "tool_node"


@pytest.mark.asyncio
async def test_direito_consumidor_first_run_reseta_flag():
    from agents.nodes import agente_direito_consumidor

    with patch("agents.nodes.model", mock_model(ai_response("Sou especialista em direito do consumidor."))):
        result = await agente_direito_consumidor(base_state(receptive_message_specialist=True))

    assert result.update.get("receptive_message_specialist") is False


@pytest.mark.asyncio
async def test_direito_consumidor_nao_first_run_nao_inclui_flag_no_update():
    from agents.nodes import agente_direito_consumidor

    with patch("agents.nodes.model", mock_model(ai_response("Como posso ajudar?"))):
        result = await agente_direito_consumidor(base_state(receptive_message_specialist=False))

    assert "receptive_message_specialist" not in result.update


# ──────────────────────────────────────────────
# tool_node — injeção de conversation_id do estado
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_node_injeta_conversation_id_do_estado(monkeypatch) -> None:
    from agents.nodes import tool_node

    retrieval = AsyncMock(return_value=[])
    monkeypatch.setattr(tools_module, "retrieval_escritorio", retrieval)

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "buscar_base_conhecimento_escritorio",
                # O LLM tentou passar outro id — deve ser ignorado.
                "args": {"query": "regimento", "conversation_id": "tenant-malicioso:123"},
                "id": "call-1",
            }
        ],
    )
    state = {"messages": [message], "conversation_id": "tenant-real:5511999998888"}

    await tool_node(state)

    retrieval.assert_awaited_once_with("tenant-real:5511999998888", "regimento")


@pytest.mark.asyncio
async def test_tool_node_injeta_saldo_do_cliente_final_em_transfer_to_specialist() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "transfer_to_specialist",
                # O LLM tentou passar saldo positivo — deve ser ignorado.
                "args": {
                    "current_specialist": "agente_condominial",
                    "end_customer_balance": 9999,
                },
                "id": "call-1",
            }
        ],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-1:5511999998888",
        "end_customer_billing": {"enabled": True, "balance": 0, "packages": []},
    }

    result = await tool_node(state)

    assert "bloqueada" in result["messages"][0].content.lower()


@pytest.mark.asyncio
async def test_tool_node_sem_end_customer_billing_no_state_nao_bloqueia() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "transfer_to_specialist", "args": {"current_specialist": "agente_contratos"}, "id": "call-1"}
        ],
    )
    state = {"messages": [message], "conversation_id": "tenant-1:5511999998888"}

    result = await tool_node(state)

    assert result.get("current_specialist") == "agente_contratos"
