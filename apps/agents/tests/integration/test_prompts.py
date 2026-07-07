"""
Testes de integração — chamam o grafo real com o modelo de linguagem.

Pré-requisito:
    OPENAI_API_KEY configurada no .env ou como variável de ambiente.

Rodar apenas estes testes:
    pytest tests/integration/ -v -s

Rodar um cenário específico:
    pytest tests/integration/test_prompts.py::test_secretaria_transfere_para_condominial -v -s
"""
import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from agents.workflow import graph
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════════════════════════════
# VARIÁVEIS — ajuste antes de rodar
# ══════════════════════════════════════════════════════════════════════

NUM_BEFORE_MESSAGES = 10

# Mensagens para testar cada cenário. Edite conforme necessário.
MENSAGENS = {
    # secretaria
    "saudacao":                  "oi, boa tarde!",
    "tema_condominial":          "o síndico do meu condomínio não está prestando contas faz meses",
    "tema_contratos":            "preciso analisar umas cláusulas do meu contrato de prestação de serviços",
    "tema_direito_consumidor":   "comprei um produto com defeito e a loja recusou a troca, quero meus direitos",

    # condominial (chamado direto, já com specialist definido)
    "condominial_prestacao_contas":  "o síndico não apresenta balancetes há 6 meses, o que posso fazer?",
    "condominial_assembleia":        "posso convocar uma assembleia extraordinária para destituir o síndico?",

    # contratos
    "contratos_clausula_multa":  "meu contrato prevê multa de 30% por desistência. Isso é válido?",
    "contratos_rescisao":        "quero rescindir meu contrato sem pagar multa, é possível?",

    # direito do consumidor
    "consumidor_produto_defeito": "comprei uma TV há 15 dias e parou de funcionar. A loja recusou a troca.",
    "consumidor_prazo_garantia":  "qual o prazo que tenho para reclamar de um produto com defeito?",
}

# ══════════════════════════════════════════════════════════════════════


async def _invoke(mensagem: str, conversation_id: str, specialist: str | None = None) -> dict:
    config = {"configurable": {"thread_id": conversation_id}}
    agent = graph.compile(checkpointer=MemorySaver())

    return await agent.ainvoke(
        {
            "messages": [HumanMessage(content=mensagem)],
            "attachments": [],
            "conversation_id": conversation_id,
            "num_before_messages": NUM_BEFORE_MESSAGES,
            "current_specialist": specialist,
            "receptive_message_specialist": specialist is not None,
        },
        config=config,
    )


def _respostas(response: dict) -> list[str]:
    return [m.content for m in response["messages"] if m.type == "ai" and m.content]


def _tools_chamadas(response: dict) -> list[str]:
    return [
        tc["name"]
        for m in response["messages"]
        if m.type == "ai" and m.tool_calls
        for tc in m.tool_calls
    ]


# ──────────────────────────────────────────────
# Secretaria — comportamento geral
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_secretaria_responde_saudacao():
    """Saudação simples não deve acionar transfer. Secretaria responde diretamente."""
    response = await _invoke(MENSAGENS["saudacao"], "prompt-secretaria-saudacao")
    respostas = _respostas(response)
    tools = _tools_chamadas(response)

    assert respostas, "Secretaria não deu nenhuma resposta"
    assert "transfer_to_specialist" not in tools, f"Não deveria transferir numa saudação. Tools: {tools}"


@pytest.mark.asyncio
async def test_secretaria_transfere_para_condominial():
    """Tema condominial deve acionar transfer para agente_condominial."""
    response = await _invoke(MENSAGENS["tema_condominial"], "prompt-secretaria-condominial")
    tools = _tools_chamadas(response)
    specialist = response.get("current_specialist")

    assert "transfer_to_specialist" in tools or specialist == "agente_condominial", (
        f"Esperava transferência para condominial.\n"
        f"Tools chamadas: {tools}\n"
        f"Specialist final: {specialist}\n"
        f"Respostas: {_respostas(response)}"
    )


@pytest.mark.asyncio
async def test_secretaria_transfere_para_contratos():
    """Tema de contratos deve acionar transfer para agente_contratos."""
    response = await _invoke(MENSAGENS["tema_contratos"], "prompt-secretaria-contratos")
    tools = _tools_chamadas(response)
    specialist = response.get("current_specialist")

    assert "transfer_to_specialist" in tools or specialist == "agente_contratos", (
        f"Esperava transferência para contratos.\n"
        f"Tools chamadas: {tools}\n"
        f"Specialist final: {specialist}\n"
        f"Respostas: {_respostas(response)}"
    )


@pytest.mark.asyncio
async def test_secretaria_transfere_para_direito_consumidor():
    """Tema de direito do consumidor deve acionar transfer para agente_direito_consumidor."""
    response = await _invoke(MENSAGENS["tema_direito_consumidor"], "prompt-secretaria-consumidor")
    tools = _tools_chamadas(response)
    specialist = response.get("current_specialist")

    assert "transfer_to_specialist" in tools or specialist == "agente_direito_consumidor", (
        f"Esperava transferência para direito_consumidor.\n"
        f"Tools chamadas: {tools}\n"
        f"Specialist final: {specialist}\n"
        f"Respostas: {_respostas(response)}"
    )


# ──────────────────────────────────────────────
# agente_condominial — qualidade de prompt
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_condominial_se_apresenta_no_primeiro_atendimento():
    """No first_run, o agente condominial deve se identificar como especialista."""
    response = await _invoke(
        MENSAGENS["condominial_prestacao_contas"],
        "prompt-condominial-apresentacao",
        specialist="agente_condominial",
    )
    texto = " ".join(_respostas(response)).lower()

    assert texto, "Agente não respondeu nada"
    assert any(p in texto for p in ["condominial", "especialista", "especialidade", "atendimento", "responsável"]), (
        f"Agente condominial não se apresentou.\nResposta: {texto}"
    )


@pytest.mark.asyncio
async def test_condominial_responde_sobre_prestacao_de_contas():
    """Agente condominial deve fornecer orientação substantiva sobre prestação de contas."""
    response = await _invoke(
        MENSAGENS["condominial_prestacao_contas"],
        "prompt-condominial-prestacao",
        specialist="agente_condominial",
    )
    respostas = _respostas(response)

    assert respostas, "Agente não respondeu"
    assert any(len(r) > 50 for r in respostas), (
        f"Resposta muito curta para o tema. Resposta: {respostas}"
    )


@pytest.mark.asyncio
async def test_condominial_responde_sobre_assembleia():
    response = await _invoke(
        MENSAGENS["condominial_assembleia"],
        "prompt-condominial-assembleia",
        specialist="agente_condominial",
    )
    respostas = _respostas(response)

    assert respostas, "Agente não respondeu"
    assert any(len(r) > 50 for r in respostas)


# ──────────────────────────────────────────────
# agente_contratos — qualidade de prompt
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_contratos_se_apresenta_no_primeiro_atendimento():
    response = await _invoke(
        MENSAGENS["contratos_clausula_multa"],
        "prompt-contratos-apresentacao",
        specialist="agente_contratos",
    )
    texto = " ".join(_respostas(response)).lower()

    assert texto, "Agente não respondeu nada"
    assert any(p in texto for p in ["contrato", "especialista", "especialidade", "atendimento", "responsável"]), (
        f"Agente contratos não se apresentou.\nResposta: {texto}"
    )


@pytest.mark.asyncio
async def test_contratos_analisa_clausula_de_multa():
    response = await _invoke(
        MENSAGENS["contratos_clausula_multa"],
        "prompt-contratos-multa",
        specialist="agente_contratos",
    )
    respostas = _respostas(response)

    assert respostas, "Agente não respondeu"
    assert any(len(r) > 50 for r in respostas), (
        f"Resposta insuficiente para análise de cláusula. Resposta: {respostas}"
    )


@pytest.mark.asyncio
async def test_contratos_orienta_sobre_rescisao():
    response = await _invoke(
        MENSAGENS["contratos_rescisao"],
        "prompt-contratos-rescisao",
        specialist="agente_contratos",
    )
    respostas = _respostas(response)

    assert respostas, "Agente não respondeu"
    assert any(len(r) > 50 for r in respostas)


# ──────────────────────────────────────────────
# agente_direito_consumidor — qualidade de prompt
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_direito_consumidor_se_apresenta_no_primeiro_atendimento():
    response = await _invoke(
        MENSAGENS["consumidor_produto_defeito"],
        "prompt-consumidor-apresentacao",
        specialist="agente_direito_consumidor",
    )
    texto = " ".join(_respostas(response)).lower()

    assert texto, "Agente não respondeu nada"
    assert any(p in texto for p in ["consumidor", "especialista", "especialidade", "atendimento", "direito", "responsável"]), (
        f"Agente direito_consumidor não se apresentou.\nResposta: {texto}"
    )


@pytest.mark.asyncio
async def test_direito_consumidor_orienta_sobre_produto_com_defeito():
    response = await _invoke(
        MENSAGENS["consumidor_produto_defeito"],
        "prompt-consumidor-defeito",
        specialist="agente_direito_consumidor",
    )
    respostas = _respostas(response)

    assert respostas, "Agente não respondeu"
    assert any(len(r) > 50 for r in respostas), (
        f"Resposta insuficiente. Resposta: {respostas}"
    )


@pytest.mark.asyncio
async def test_direito_consumidor_orienta_sobre_prazo_de_garantia():
    response = await _invoke(
        MENSAGENS["consumidor_prazo_garantia"],
        "prompt-consumidor-prazo",
        specialist="agente_direito_consumidor",
    )
    respostas = _respostas(response)

    assert respostas, "Agente não respondeu"
    assert any(len(r) > 50 for r in respostas)
