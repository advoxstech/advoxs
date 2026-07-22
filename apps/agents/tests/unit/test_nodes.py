import pytest
from unittest.mock import AsyncMock, patch
from langchain_core.messages import AIMessage
from langgraph.graph import END
from tests.factories import ai_with_tool_call, ai_response, mock_model, base_state

import agents.tools as tools_module


# ──────────────────────────────────────────────
# agent_node — ponto de entrada (current_agent_id=None)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entry_point_sem_tool_calls_vai_para_end():
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response("Olá, como posso ajudar?"))):
        result = await agent_node(base_state())

    assert result.goto == END
    assert result.update["current_agent_id"] == "entry-1"


@pytest.mark.asyncio
async def test_entry_point_com_tool_call_vai_para_tool_node():
    from agents.nodes import agent_node
    fake = ai_with_tool_call("transfer_to_agent", {"agent_id": "other-1"})

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(base_state())

    assert result.goto == "tool_node"


@pytest.mark.asyncio
async def test_current_agent_id_invalido_cai_no_ponto_de_entrada():
    """Um current_agent_id que não existe mais na lista (agente apagado,
    checkpoint de antes do deploy) cai no fallback do ponto de entrada."""
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response("oi"))):
        result = await agent_node(base_state(current_agent_id="agente-apagado"))

    assert result.update["current_agent_id"] == "entry-1"


@pytest.mark.asyncio
async def test_sem_agentes_no_estado_retorna_erro_generico():
    from agents.nodes import agent_node

    result = await agent_node(base_state(agents=[]))

    assert result.goto == END
    assert result.update["messages"][0].content != ""


@pytest.mark.asyncio
async def test_transfer_sem_content_injeta_despedida_com_nome_do_agente():
    from agents.nodes import agent_node
    fake = ai_with_tool_call("transfer_to_agent", {"agent_id": "other-1"}, content="")

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(base_state())

    ai_msg = result.update["messages"][0]
    assert ai_msg.content != "", "Despedida não foi injetada"
    assert "condominial" in ai_msg.content.lower()


@pytest.mark.asyncio
async def test_transfer_com_content_nao_sobrescreve():
    from agents.nodes import agent_node
    fake = ai_with_tool_call(
        "transfer_to_agent", {"agent_id": "other-1"}, content="Um momento, vou transferir você."
    )

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(base_state())

    ai_msg = result.update["messages"][0]
    assert ai_msg.content == "Um momento, vou transferir você."


@pytest.mark.asyncio
async def test_transfer_tool_call_mantem_tool_calls_na_mensagem():
    from agents.nodes import agent_node
    fake = ai_with_tool_call("transfer_to_agent", {"agent_id": "other-1"})

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(base_state())

    ai_msg = result.update["messages"][0]
    assert ai_msg.tool_calls, "tool_calls não foram preservados na mensagem"
    assert ai_msg.tool_calls[0]["name"] == "transfer_to_agent"


@pytest.mark.asyncio
async def test_bind_inclui_gerar_link_pagamento_quando_billing_habilitado(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agent_node(base_state(end_customer_billing={"enabled": True, "balance": 500, "packages": []}))

    bound_tools = model.bind_tools.call_args.args[0]
    tool_names = {t.name for t in bound_tools}
    assert "gerar_link_pagamento_cliente" in tool_names


@pytest.mark.asyncio
async def test_bind_nao_inclui_gerar_link_pagamento_quando_billing_desabilitado(monkeypatch) -> None:
    """A mera presença da tool no bind_tools já muda o comportamento de
    function-calling do modelo (visto num teste de integração real) — por
    isso ela só entra na lista quando a feature está de fato ligada pro
    tenant, nunca incondicionalmente."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agent_node(base_state(end_customer_billing={"enabled": False, "balance": 0, "packages": []}))

    bound_tools = model.bind_tools.call_args.args[0]
    tool_names = {t.name for t in bound_tools}
    assert "gerar_link_pagamento_cliente" not in tool_names


@pytest.mark.asyncio
async def test_bind_nao_inclui_gerar_link_pagamento_sem_end_customer_billing_no_state(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agent_node(base_state())

    bound_tools = model.bind_tools.call_args.args[0]
    tool_names = {t.name for t in bound_tools}
    assert "gerar_link_pagamento_cliente" not in tool_names


# ──────────────────────────────────────────────
# agent_node — roster de outros agentes no prompt (transferência dinâmica)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prompt_inclui_roster_de_outros_agentes_para_transferencia(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agent_node(base_state())

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "other-1" in prompt_arg.content
    assert "Condominial" in prompt_arg.content


@pytest.mark.asyncio
async def test_prompt_nao_inclui_o_proprio_agente_no_roster(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agent_node(base_state())

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "entry-1" not in prompt_arg.content


@pytest.mark.asyncio
async def test_sem_outros_agentes_nao_inclui_bloco_de_roster(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(agents=[{
        "id": "entry-1",
        "name": "Secretária",
        "instructions": "Você é a secretária de triagem.",
        "is_entry_point": True,
        "knowledge_base_file_ids": [],
    }])
    await agent_node(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Agentes disponíveis para transferência" not in prompt_arg.content


@pytest.mark.asyncio
async def test_injeta_pacotes_no_prompt_quando_sem_saldo_no_ponto_de_entrada(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": True,
            "balance": 0,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agent_node(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" in prompt_arg.content
    assert "p-1" in prompt_arg.content


@pytest.mark.asyncio
async def test_instrui_a_nao_revelar_package_id_ao_cliente(monkeypatch) -> None:
    """Bug real reportado pelo usuário: a secretária repetiu o package_id
    (um uuid grande) na mensagem pro cliente, porque a instrução nunca
    dizia que esse id é só de uso interno."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": True,
            "balance": 0,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agent_node(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "NUNCA mencione o package_id ao cliente" in prompt_arg.content


@pytest.mark.asyncio
async def test_instrui_a_colar_o_link_retornado_na_resposta(monkeypatch) -> None:
    """Bug real reportado pelo usuário: a secretária disse 'gerei o link de
    pagamento' sem colar o link de verdade, porque a instrução nunca dizia
    explicitamente pra copiar o retorno da tool na resposta ao cliente."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": True,
            "balance": 0,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agent_node(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "copie esse link literalmente na sua resposta ao cliente" in prompt_arg.content


@pytest.mark.asyncio
async def test_nao_injeta_pacotes_quando_billing_desabilitado(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": False,
            "balance": 0,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agent_node(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" not in prompt_arg.content
    assert "Pacotes disponíveis" not in prompt_arg.content


@pytest.mark.asyncio
async def test_nao_injeta_pacotes_com_saldo_positivo(monkeypatch) -> None:
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    state = base_state(
        end_customer_billing={
            "enabled": True,
            "balance": 500,
            "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
        }
    )
    await agent_node(state)

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" not in prompt_arg.content


@pytest.mark.asyncio
async def test_transfer_sem_content_pula_despedida_quando_bloqueado():
    """Quando a transferência vai ser bloqueada (sem saldo), não injeta despedida —
    o tool_node ainda vai rodar e transfer_to_agent vai recusar, então a despedida
    ("vou te passar agora") ficaria contraditória."""
    from agents.nodes import agent_node
    fake = ai_with_tool_call("transfer_to_agent", {"agent_id": "other-1"}, content="")

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(
            base_state(end_customer_billing={"enabled": True, "balance": 0, "packages": []})
        )

    ai_msg = result.update["messages"][0]
    assert ai_msg.content == ""
    assert ai_msg.tool_calls, "tool_calls devem ser preservados mesmo sem despedida"
    assert result.goto == "tool_node"


# ──────────────────────────────────────────────
# agent_node — agente não-entry-point (equivalente aos especialistas de antes)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agente_atual_sem_tool_calls_sem_content_vai_para_end():
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response(""))):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=False))

    assert result.goto == END
    assert result.update["current_agent_id"] == "other-1"


@pytest.mark.asyncio
async def test_agente_atual_com_tool_call_vai_para_tool_node():
    from agents.nodes import agent_node
    fake = ai_with_tool_call("transfer_to_agent", {"agent_id": "entry-1"})

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=False))

    assert result.goto == "tool_node"


@pytest.mark.asyncio
async def test_agente_atual_com_content_sem_tool_vai_para_end():
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response("Vou te ajudar com o condomínio."))):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=False))

    assert result.goto == END


@pytest.mark.asyncio
async def test_agente_atual_first_run_reseta_flag():
    """receptive_message_specialist deve ser False no update após first_run=True."""
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response("Olá! Sou o especialista condominial."))):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=True))

    assert result.update.get("receptive_message_specialist") is False


@pytest.mark.asyncio
async def test_agente_atual_nao_first_run_nao_inclui_flag_no_update():
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response(""))):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=False))

    assert "receptive_message_specialist" not in result.update


@pytest.mark.asyncio
async def test_ponto_de_entrada_nunca_recebe_instrucao_de_first_run(monkeypatch):
    """O ponto de entrada nunca ganha a instrução de 'primeira resposta' — mesmo
    que receptive_message_specialist venha True por engano no estado."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("oi"))
    monkeypatch.setattr("agents.nodes.model", model)

    await agent_node(base_state(current_agent_id=None, receptive_message_specialist=True))

    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "primeira resposta" not in prompt_arg.content.lower()


@pytest.mark.asyncio
async def test_saldo_esgotado_no_meio_da_conversa_devolve_pro_ponto_de_entrada(monkeypatch) -> None:
    """Saldo esgotado no meio da conversa (não só na transferência inicial) deve
    ser atendido pelo ponto de entrada (equivalente à antiga secretária), que
    oferece os pacotes — em vez de deixar o agente atual responder de graça."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("aqui estão os pacotes disponíveis"))
    monkeypatch.setattr("agents.nodes.model", model)

    result = await agent_node(
        base_state(
            current_agent_id="other-1",
            receptive_message_specialist=False,
            end_customer_billing={
                "enabled": True,
                "balance": 0,
                "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
            },
        )
    )

    assert result.update["current_agent_id"] == "entry-1"
    model.bind_tools.assert_called_once()
    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" in prompt_arg.content

    # Aviso fixo de retorno vem antes da resposta normal do ponto de entrada.
    assert len(result.update["messages"]) == 2
    aviso, resposta = result.update["messages"]
    assert aviso.content == (
        "voltando para Secretária — o atendimento anterior ficou indisponível "
        "porque os créditos acabaram."
    )
    assert resposta.content == "aqui estão os pacotes disponíveis"


@pytest.mark.asyncio
async def test_aviso_de_retorno_nao_repete_quando_ja_esta_no_ponto_de_entrada(monkeypatch) -> None:
    """O aviso de retorno só deve aparecer no turno exato da transição
    especialista -> ponto de entrada. Nos turnos seguintes, com
    current_agent_id já apontando pro ponto de entrada, a condição de
    bloqueio (`not current["is_entry_point"]`) nunca mais é verdadeira —
    então o aviso não deve se repetir."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("aqui estão os pacotes disponíveis"))
    monkeypatch.setattr("agents.nodes.model", model)

    result = await agent_node(
        base_state(
            current_agent_id="entry-1",
            receptive_message_specialist=False,
            end_customer_billing={
                "enabled": True,
                "balance": 0,
                "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
            },
        )
    )

    assert result.update["current_agent_id"] == "entry-1"
    assert len(result.update["messages"]) == 1
    assert result.update["messages"][0].content == "aqui estão os pacotes disponíveis"


@pytest.mark.asyncio
async def test_agente_com_saldo_positivo_nao_e_bloqueado():
    """Billing habilitado mas com saldo positivo não deve bloquear — fluxo normal."""
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response("Analisando seu caso."))):
        result = await agent_node(
            base_state(
                current_agent_id="other-1",
                receptive_message_specialist=False,
                end_customer_billing={"enabled": True, "balance": 500, "packages": []},
            )
        )

    assert result.goto == END
    assert result.update["current_agent_id"] == "other-1"
    assert result.update["messages"][0].content == "Analisando seu caso."


@pytest.mark.asyncio
async def test_agente_sem_billing_no_state_nao_bloqueia():
    """Sem end_customer_billing no state (fluxo normal de escritório, sem
    cobrança de cliente final), o agente segue chamando o LLM normalmente."""
    from agents.nodes import agent_node

    with patch("agents.nodes.model", mock_model(ai_response("Vou orientar você."))):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=False))

    assert result.goto == END
    assert result.update["messages"][0].content == "Vou orientar você."


@pytest.mark.asyncio
async def test_transfer_sem_content_injeta_despedida_no_agente_atual():
    from agents.nodes import agent_node
    fake = ai_with_tool_call("transfer_to_agent", {"agent_id": "entry-1"}, content="")

    with patch("agents.nodes.model", mock_model(fake)):
        result = await agent_node(base_state(current_agent_id="other-1", receptive_message_specialist=False))

    ai_msg = result.update["messages"][0]
    assert ai_msg.content != ""
    assert "secretária" in ai_msg.content.lower()


# ──────────────────────────────────────────────
# tool_node — injeção de conversation_id, KB do agente e transferência
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_node_injeta_conversation_id_do_estado(monkeypatch) -> None:
    from agents.nodes import tool_node

    retrieval = AsyncMock(return_value=[])
    monkeypatch.setattr(tools_module, "retrieval_usuario", retrieval)

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "bucar_base_conhecimento_usuario",
                # O LLM tentou passar outro id — deve ser ignorado.
                "args": {"query": "meu contrato", "conversation_id": "tenant-malicioso:123"},
                "id": "call-1",
            }
        ],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-real:5511999998888",
        "agents": base_state()["agents"],
    }

    await tool_node(state)

    retrieval.assert_awaited_once_with("tenant-real:5511999998888", "meu contrato")


@pytest.mark.asyncio
async def test_tool_node_injeta_knowledge_base_file_ids_do_agente_atual(monkeypatch) -> None:
    from agents.nodes import tool_node

    retrieval = AsyncMock(return_value=[])
    monkeypatch.setattr(tools_module, "retrieval_escritorio", retrieval)

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "buscar_base_conhecimento_agente",
                # O LLM tentou passar outros ids — deve ser ignorado.
                "args": {"query": "regimento", "knowledge_base_file_ids": ["arquivo-forjado"]},
                "id": "call-1",
            }
        ],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-real:5511999998888",
        "current_agent_id": "other-1",
        "agents": base_state()["agents"],
    }

    await tool_node(state)

    retrieval.assert_awaited_once_with(
        "tenant-real:5511999998888", "regimento", doc_ids=["kb-1"]
    )


@pytest.mark.asyncio
async def test_tool_node_injeta_valid_agent_ids_em_transfer_to_agent() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "transfer_to_agent",
                # O LLM tentou transferir pra um id que não existe.
                "args": {"agent_id": "agente-forjado"},
                "id": "call-1",
            }
        ],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-1:5511999998888",
        "agents": base_state()["agents"],
    }

    result = await tool_node(state)

    assert "recusada" in result["messages"][0].content.lower()
    assert "current_agent_id" not in result


@pytest.mark.asyncio
async def test_tool_node_transfer_to_agent_valido_atualiza_estado() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[{"name": "transfer_to_agent", "args": {"agent_id": "other-1"}, "id": "call-1"}],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-1:5511999998888",
        "agents": base_state()["agents"],
    }

    result = await tool_node(state)

    assert result["current_agent_id"] == "other-1"
    assert result["receptive_message_specialist"] is True


@pytest.mark.asyncio
async def test_tool_node_injeta_saldo_do_cliente_final_em_transfer_to_agent() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "transfer_to_agent",
                # O LLM tentou passar saldo positivo — deve ser ignorado.
                "args": {"agent_id": "other-1", "end_customer_balance": 9999},
                "id": "call-1",
            }
        ],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-1:5511999998888",
        "agents": base_state()["agents"],
        "end_customer_billing": {"enabled": True, "balance": 0, "packages": []},
    }

    result = await tool_node(state)

    assert "bloqueada" in result["messages"][0].content.lower()


@pytest.mark.asyncio
async def test_tool_node_sem_end_customer_billing_no_state_nao_bloqueia() -> None:
    from agents.nodes import tool_node

    message = AIMessage(
        content="",
        tool_calls=[{"name": "transfer_to_agent", "args": {"agent_id": "other-1"}, "id": "call-1"}],
    )
    state = {
        "messages": [message],
        "conversation_id": "tenant-1:5511999998888",
        "agents": base_state()["agents"],
    }

    result = await tool_node(state)

    assert result.get("current_agent_id") == "other-1"
