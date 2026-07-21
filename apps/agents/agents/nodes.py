from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from agents.helpers import strip_messages
from agents.tools import *

from dotenv import load_dotenv
from langgraph.graph import END
from langgraph.types import Command
from loguru import logger

load_dotenv()

model = ChatOpenAI(model="gpt-5-mini-2025-08-07", temperature=0)

# Tools cujo conversation_id vem SEMPRE do estado do grafo, nunca do LLM —
# o tenant_id vive dentro dele (isolamento multi-tenant).
STATE_SCOPED_TOOLS = {
    "bucar_base_conhecimento_usuario",
    "buscar_base_conhecimento_agente",
    "gerar_link_pagamento_cliente",
}
# Saldo/enabled do cliente final: nunca confiar em valor vindo do LLM.
BILLING_GATED_TOOLS = {"transfer_to_agent"}


async def agent_node(state: dict) -> Command:
    agents_by_id = {a["id"]: a for a in state.get("agents", [])}
    entry_point = next((a for a in state.get("agents", []) if a.get("is_entry_point")), None)

    if not agents_by_id or entry_point is None:
        logger.error("Nenhum agente disponível no estado — tenant sem agentes configurados")
        return Command(
            update={
                "messages": [
                    AIMessage(content="Desculpe, houve um erro ao processar sua mensagem.")
                ]
            },
            goto=END,
        )

    current_agent_id = state.get("current_agent_id")
    current = agents_by_id.get(current_agent_id) if current_agent_id else None
    if current is None:
        current = entry_point

    billing = state.get("end_customer_billing") or {}
    billing_enabled = bool(billing.get("enabled"))
    billing_blocked = is_billing_blocked(billing.get("enabled"), billing.get("balance", 0))

    if billing_blocked and not current["is_entry_point"]:
        logger.info(
            "Agente bloqueado por saldo esgotado, devolvendo pro ponto de entrada | agent_id={}",
            current["id"],
        )
        current = entry_point

    is_entry_point = current["is_entry_point"]
    # O ponto de entrada nunca recebe a instrução de "primeira resposta" —
    # esse conceito é só do agente que ACABOU de receber uma transferência
    # (equivalente à antiga distinção secretária vs. especialista).
    is_first_run = bool(state.get("receptive_message_specialist", False)) and not is_entry_point

    logger.info(
        "agent_node chamado | agent_id={} | mensagens={} | histórico={} | first_run={}",
        current["id"],
        len(state["messages"]),
        state["num_before_messages"],
        is_first_run,
    )

    last_messages = strip_messages(state["messages"], state["num_before_messages"])

    # gerar_link_pagamento_cliente só é bindada quando a cobrança do cliente
    # final está de fato habilitada pro tenant — do contrário, a mera presença
    # da tool na lista já muda o comportamento de function-calling do modelo
    # (verificado num teste de integração real: o modelo passou a pedir uma
    # pergunta de esclarecimento antes de transferir mesmo sem a feature
    # habilitada, só por ter uma tool a mais disponível).
    tools_for_agent = [transfer_to_agent, buscar_base_conhecimento_agente, bucar_base_conhecimento_usuario]
    if billing_enabled:
        tools_for_agent.append(gerar_link_pagamento_cliente)
    model_with_tools = model.bind_tools(tools_for_agent)

    prompt = current["instructions"]
    other_agents = [a for a in state.get("agents", []) if a["id"] != current["id"]]
    if other_agents:
        roster_text = "\n".join(f"- agent_id: {a['id']} — {a['name']}" for a in other_agents)
        prompt += (
            "\n\n---\n"
            "**Agentes disponíveis para transferência** (use o agent_id exato ao "
            "chamar transfer_to_agent — nunca invente ou abrevie o id):\n"
            f"{roster_text}"
        )

    if billing_blocked and is_entry_point:
        packages_text = "\n".join(
            f"- {p['name']}: R$ {p['price_brl']} = {p['credits_granted']} créditos "
            f"(package_id: {p['id']})"
            for p in billing.get("packages", [])
        )
        prompt += (
            "\n\n---\n"
            "**Instrução:** Este cliente está sem créditos disponíveis. Antes de "
            "transferir para outro agente, explique que é necessário comprar "
            "créditos e ofereça os pacotes abaixo. Quando o cliente escolher um, "
            "use a tool gerar_link_pagamento_cliente com o package_id correspondente. "
            "Depois que o cliente confirmar que pagou, chame transfer_to_agent "
            "de novo — é essa chamada que efetivamente libera o outro agente; nunca "
            "diga que já transferiu sem chamar essa ferramenta.\n\n"
            f"Pacotes disponíveis:\n{packages_text}"
        )
    if is_first_run:
        prompt += (
            "\n\n---\n"
            "**Instrução:** Esta é sua primeira resposta neste atendimento. "
            "### Se Apresente, diga sua especialidade e diga que dali para frente é responsável pelo atendimento. "
            "Leia o histórico completo e responda diretamente com seu parecer sobre o caso. "
        )

    response = await model_with_tools.ainvoke([
        SystemMessage(content=prompt),
        *last_messages,
    ])

    update: dict = {"messages": [response], "current_agent_id": current["id"]}
    if is_first_run:
        update["receptive_message_specialist"] = False

    if response.tool_calls:
        tool_name = response.tool_calls[0]["name"]
        logger.info("Ferramenta selecionada | tool={}", tool_name)

        if tool_name == "transfer_to_agent" and not response.content and not billing_blocked:
            target_id = response.tool_calls[0]["args"].get("agent_id")
            target = agents_by_id.get(target_id)
            label = target["name"] if target else "outro agente"
            farewell = f"um momento... vou te passar pra(o) {label} agora."
            response = AIMessage(content=farewell, tool_calls=response.tool_calls, id=response.id)
            update["messages"] = [response]
            logger.info("Despedida de transferência injetada | target={}", target_id)

        return Command(update=update, goto="tool_node")

    logger.info("Modelo respondeu sem chamar ferramentas")
    return Command(update=update, goto=END)


async def tool_node(state: dict) -> dict:
    logger.info("tool_node chamado")

    tools_by_name = {tool.name: tool for tool in tools}
    tool_calls = state["messages"][-1].tool_calls
    logger.info("Processando {} tool call(s)", len(tool_calls))

    agents_by_id = {a["id"]: a for a in state.get("agents", [])}

    messages = []
    state_updates = {}

    for tool_call in tool_calls:
        tool = tools_by_name.get(tool_call["name"])

        if tool is None:
            logger.warning("Ferramenta não encontrada | tool={}", tool_call["name"])
            continue

        args = dict(tool_call["args"])
        if tool_call["name"] in STATE_SCOPED_TOOLS:
            args["conversation_id"] = state["conversation_id"]
        if tool_call["name"] == "buscar_base_conhecimento_agente":
            current = agents_by_id.get(state.get("current_agent_id"))
            args["knowledge_base_file_ids"] = (current or {}).get("knowledge_base_file_ids", [])
        if tool_call["name"] == "transfer_to_agent":
            args["valid_agent_ids"] = list(agents_by_id.keys())
        if tool_call["name"] in BILLING_GATED_TOOLS:
            billing = state.get("end_customer_billing") or {}
            args["end_customer_billing_enabled"] = bool(billing.get("enabled"))
            args["end_customer_balance"] = billing.get("balance", 0)

        logger.info("Executando ferramenta | tool={} | args={}", tool_call["name"], args)
        observation = await tool.ainvoke(args)
        logger.info("Ferramenta concluída | tool={}", tool_call["name"])

        if isinstance(observation, Command):
            if observation.update:
                logger.info("Atualizando estado via Command | updates={}", list(observation.update.keys()))
                state_updates.update(observation.update)
            content = ""
        else:
            content = str(observation)

        messages.append(ToolMessage(content=content, tool_call_id=tool_call["id"]))

    logger.info("tool_node finalizado | mensagens_geradas={}", len(messages))
    return {"messages": messages, **state_updates}
