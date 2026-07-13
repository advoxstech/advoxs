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
    "buscar_base_conhecimento_escritorio",
    "gerar_link_pagamento_cliente",
}
# Saldo/enabled do cliente final: nunca confiar em valor vindo do LLM.
BILLING_GATED_TOOLS = {"transfer_to_specialist"}


async def agente_secretaria(state: dict) -> dict:
    logger.info(
        "agente_secretaria chamado | mensagens={} | histórico={}",
        len(state["messages"]),
        state["num_before_messages"],
    )

    last_messages = strip_messages(state["messages"], state["num_before_messages"])

    billing = state.get("end_customer_billing") or {}
    billing_enabled = bool(billing.get("enabled"))
    billing_blocks_transfer = is_billing_blocked(billing.get("enabled"), billing.get("balance", 0))

    # gerar_link_pagamento_cliente só é bindada quando a cobrança do cliente
    # final está de fato habilitada pro tenant — do contrário, a mera presença
    # da tool na lista já muda o comportamento de function-calling do modelo
    # (verificado num teste de integração real: a secretária passou a pedir
    # uma pergunta de esclarecimento antes de transferir mesmo sem a feature
    # habilitada, só por ter uma tool a mais disponível).
    tools_secretaria = [transfer_to_specialist, buscar_base_conhecimento_escritorio]
    if billing_enabled:
        tools_secretaria.append(gerar_link_pagamento_cliente)
    model_with_tools = model.bind_tools(tools_secretaria)

    with open("agents/prompts/secretaria.md", "r", encoding="utf-8") as arquivo:
        prompt = arquivo.read()
    if billing_blocks_transfer:
        packages_text = "\n".join(
            f"- {p['name']}: R$ {p['price_brl']} = {p['credits_granted']} créditos "
            f"(package_id: {p['id']})"
            for p in billing.get("packages", [])
        )
        prompt += (
            "\n\n---\n"
            "**Instrução:** Este cliente está sem créditos disponíveis. Antes de "
            "transferir para um especialista, explique que é necessário comprar "
            "créditos e ofereça os pacotes abaixo. Quando o cliente escolher um, "
            "use a tool gerar_link_pagamento_cliente com o package_id correspondente.\n\n"
            f"Pacotes disponíveis:\n{packages_text}"
        )

    response = await model_with_tools.ainvoke([
        SystemMessage(content=prompt),
        *last_messages,
    ])

    if response.tool_calls:
        tool_name = response.tool_calls[0]["name"]
        logger.info("Ferramenta selecionada | tool={}", tool_name)

        if tool_name == "transfer_to_specialist" and not response.content and not billing_blocks_transfer:
            specialist = response.tool_calls[0]["args"].get("current_specialist", "especialista")
            label = specialist.replace("agente_", "").replace("_", " ")
            farewell = f"um momento... vou te passar pro especialista de {label} agora."
            response = AIMessage(content=farewell, tool_calls=response.tool_calls, id=response.id)
            logger.info("Despedida de transferência injetada | specialist={}", specialist)

        return Command(update={"messages": [response]}, goto="tool_node")

    logger.info("Modelo respondeu sem chamar ferramentas")
    return Command(update={"messages": [response]}, goto=END)


async def agente_condominial(state: dict) -> Command:
    billing = state.get("end_customer_billing") or {}
    if is_billing_blocked(billing.get("enabled"), billing.get("balance", 0)):
        logger.info(
            "Especialista bloqueado por saldo esgotado, devolvendo pra secretária | specialist={}",
            "agente_condominial",
        )
        return Command(update={"current_specialist": None}, goto="agente_secretaria")

    is_first_run = state.get("receptive_message_specialist", False)
    logger.info(
        "agente_condominial chamado | mensagens={} | histórico={} | first_run={}",
        len(state["messages"]),
        state["num_before_messages"],
        is_first_run,
    )

    last_messages = strip_messages(state["messages"], state["num_before_messages"])
    model_with_tools = model.bind_tools([transfer_to_specialist, bucar_base_conhecimento_condominial, bucar_base_conhecimento_usuario, buscar_base_conhecimento_escritorio])

    with open("agents/prompts/condominial.md", "r", encoding="utf-8") as arquivo:
        prompt = arquivo.read()

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

    update = {"messages": [response]}
    if is_first_run:
        update["receptive_message_specialist"] = False

    if response.tool_calls:
        tool_name = response.tool_calls[0]["name"]  
        logger.info("Ferramenta selecionada | tool={}", response.tool_calls[0]["name"])

        if tool_name == "transfer_to_specialist" and not response.content:
            specialist = response.tool_calls[0]["args"].get("current_specialist", "especialista")
            label = specialist.replace("agente_", "").replace("_", " ")
            farewell = f"um momento... vou te passar pro especialista de {label} agora."
            response = AIMessage(content=farewell, tool_calls=response.tool_calls, id=response.id)
            logger.info("Despedida de transferência injetada | specialist={}", specialist)

        return Command(update={"messages": [response]}, goto="tool_node")

    logger.info("Modelo respondeu sem chamar ferramentas")
    return Command(update=update, goto=END)


async def agente_contratos(state: dict) -> Command:
    billing = state.get("end_customer_billing") or {}
    if is_billing_blocked(billing.get("enabled"), billing.get("balance", 0)):
        logger.info(
            "Especialista bloqueado por saldo esgotado, devolvendo pra secretária | specialist={}",
            "agente_contratos",
        )
        return Command(update={"current_specialist": None}, goto="agente_secretaria")

    is_first_run = state.get("receptive_message_specialist", False)
    logger.info(
        "agente_contratos chamado | mensagens={} | histórico={} | first_run={}",
        len(state["messages"]),
        state["num_before_messages"],
        is_first_run,
    )

    last_messages = strip_messages(state["messages"], state["num_before_messages"])
    model_with_tools = model.bind_tools([transfer_to_specialist, bucar_base_conhecimento_contratos, bucar_base_conhecimento_usuario, buscar_base_conhecimento_escritorio])

    with open("agents/prompts/contratos.md", "r", encoding="utf-8") as arquivo:
        prompt = arquivo.read()

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

    update = {"messages": [response]}
    if is_first_run:
        update["receptive_message_specialist"] = False

    if response.tool_calls:
        logger.info("Ferramenta selecionada | tool={}", response.tool_calls[0]["name"])
        return Command(update=update, goto="tool_node")

    logger.info("Modelo respondeu sem chamar ferramentas")
    return Command(update=update, goto=END)


async def agente_direito_consumidor(state: dict) -> Command:
    billing = state.get("end_customer_billing") or {}
    if is_billing_blocked(billing.get("enabled"), billing.get("balance", 0)):
        logger.info(
            "Especialista bloqueado por saldo esgotado, devolvendo pra secretária | specialist={}",
            "agente_direito_consumidor",
        )
        return Command(update={"current_specialist": None}, goto="agente_secretaria")

    is_first_run = state.get("receptive_message_specialist", False)
    logger.info(
        "agente_direito_consumidor chamado | mensagens={} | histórico={} | first_run={}",
        len(state["messages"]),
        state["num_before_messages"],
        is_first_run,
    )

    last_messages = strip_messages(state["messages"], state["num_before_messages"])
    model_with_tools = model.bind_tools([transfer_to_specialist, bucar_base_conhecimento_direito_consumidor, bucar_base_conhecimento_usuario, buscar_base_conhecimento_escritorio])

    with open("agents/prompts/direito_consumidor.md", "r", encoding="utf-8") as arquivo:
        prompt = arquivo.read()

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

    update = {"messages": [response]}
    if is_first_run:
        update["receptive_message_specialist"] = False

    if response.tool_calls:
        logger.info("Ferramenta selecionada | tool={}", response.tool_calls[0]["name"])
        return Command(update=update, goto="tool_node")

    logger.info("Modelo respondeu sem chamar ferramentas")
    return Command(update=update, goto=END)


async def tool_node(state: dict) -> dict:
    logger.info("tool_node chamado")

    tools_by_name = {tool.name: tool for tool in tools}
    tool_calls = state["messages"][-1].tool_calls
    logger.info("Processando {} tool call(s)", len(tool_calls))

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
