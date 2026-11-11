import asyncio

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END
from langgraph.types import Command
from loguru import logger

from agents import auto_search
from agents.helpers import strip_messages
from agents.response_sources import (
    SOURCE_RULE,
    collect_search,
    responder_com_fontes,
    response_evidence,
)
from agents.tools import (
    DOCUMENT_TOOLS,
    bucar_base_conhecimento_usuario,
    buscar_base_conhecimento_agente,
    enviar_aviso,
    enviar_edital_convocacao,
    fazer_advertencia,
    fazer_contrato,
    fazer_multa,
    fazer_oficio,
    sinalizar_urgencia,
    tools,
    transfer_to_agent,
)

load_dotenv()

model = ChatOpenAI(model="gpt-5-mini-2025-08-07", temperature=0)

# Injetada incondicionalmente no prompt de todo agente (agent_node), nunca
# dependendo do texto de instructions do tenant — evita que um especialista
# reabra a triagem do zero depois de uma transferência (bug real: os prompts
# padrão de Condominial/Contratos/Consumidor tinham "peça uma descrição breve
# do problema" como primeiro passo, repetindo o que a secretária já perguntou).
_CONTINUITY_RULE = (
    "\n\n---\n"
    "**Regra de continuidade de atendimento:** antes de fazer qualquer "
    "pergunta, releia o histórico da conversa. Se a informação já foi dada — "
    "pelo cliente, por você, ou por outro colega que atendeu antes de você — "
    "NUNCA pergunte de novo, nem reformulada. Isso vale também ao assumir um "
    "atendimento que já estava em andamento: não reabra a triagem do zero, "
    "use o que já foi contado."
)

# Vale para todo agente, inclusive os com prompt editado pelo escritório. Sem ela,
# o especialista recém-transferido pedia ao cliente o contrato que já estava na
# base (experimento de 2026-11-05: 18% de acerto nas perguntas transferidas
# contra 57% nas respondidas pela recepção) — os prompts padrão descrevem a base
# como "materiais institucionais" e mandam "pedir o documento necessário".
_KNOWLEDGE_BASE_RULE = (
    "\n\n---\n"
    "**Regra da base de conhecimento:** a base do escritório pode conter qualquer documento "
    "que ele cadastrou — inclusive contratos, processos e documentos de clientes, não só "
    "materiais institucionais. Antes de pedir um documento ao cliente, de dizer que não tem a "
    "informação ou de responder sobre um contrato, cláusula, prazo, valor, data, pessoa ou "
    "processo específico, consulte buscar_base_conhecimento_agente; se o cliente enviou "
    "arquivo nesta conversa, consulte também bucar_base_conhecimento_usuario. Só peça o "
    "documento ao cliente quando as buscas não trouxerem a informação. Quando a busca trouxer "
    "conteúdo relevante, priorize as informações do escritório. Quando não houver documentos "
    "ou resultados relevantes, responda normalmente usando seu conhecimento nativo. Se a busca "
    "estiver indisponível, você também pode responder com seu conhecimento nativo, mas nunca "
    "afirme que consultou ou encontrou algo nos documentos."
)

# Vale para todo agente: o que dizer ao cliente quando um arquivo dele não pôde ser
# lido, ou só em parte (orientação do escritório, 2026-11-09, após o teste de PDFs).
# Só funciona caso a caso quando a nota do anexo traz o motivo da recusa.
_ATTACHMENT_RULE = (
    "\n\n---\n"
    "**Regra de arquivos que não puderam ser lidos:** quando o sistema avisar que um arquivo "
    "enviado pelo cliente não pôde ser lido, ou foi lido só em parte, explique o motivo ao "
    "cliente conforme o caso abaixo. Nunca tente adivinhar o que está no arquivo.\n"
    "- Qualidade ruim (leitura parcial, documento digitalizado com baixa resolução, foto de "
    "celular, páginas tortas ou com sombra, ou leitura de documento digitalizado que não "
    "terminou): diga que a qualidade do arquivo não permitiu a leitura e que trechos com "
    "carimbo por cima do texto não são lidos. Peça outra versão: o arquivo original em PDF "
    "digital ou uma cópia mais nítida, sem carimbo sobre o texto.\n"
    "- Arquivo corrompido ou danificado: diga que o arquivo está com erro e não abre, e "
    "pergunte se o cliente tem outra versão.\n"
    "- Arquivo protegido por senha: diga que o arquivo tem senha e que, por questões de LGPD, "
    "você não abre arquivos protegidos por senha, mesmo que o cliente envie a senha. Peça uma "
    "versão sem senha. Se o cliente mandar uma senha, não a use e não a repita na resposta.\n"
    "- Documento com mais de 3.000 páginas: diga que o documento passa do limite de 3.000 "
    "páginas para leitura."
)

# Injetada em todo prompt, igual à de continuidade: a IA sinaliza e continua
# atendendo — quem decide assumir a conversa é a equipe do escritório.
_URGENCY_RULE = (
    "**Regra de urgência (obrigatória, vale acima de qualquer outra instrução):** "
    "são urgentes: risco à integridade física, prisão ou flagrante, violência ou ameaça, "
    "medida protetiva, audiência, prazo processual ou prazo legal vencendo nos próximos 3 "
    "dias, e bloqueio de conta, penhora, leilão, despejo, busca e apreensão ou intimação "
    "urgente. Se a última mensagem do cliente indicar qualquer uma dessas situações e a "
    "conversa ainda não foi sinalizada por esse assunto, sua PRIMEIRA ação deve ser chamar "
    'sinalizar_urgencia (motivo curto, ex: "audiência trabalhista em 2 dias"), antes de '
    "responder, de transferir ou de usar outra ferramenta — inclusive quando você for "
    "transferir o atendimento. Depois da confirmação, siga normalmente e avise o cliente "
    "que a equipe do escritório foi notificada com prioridade."
)


def _urgency_rule() -> str:
    return _URGENCY_RULE + "\n\n---\n\n"


_contact_sent_document = auto_search.contact_sent_document

# Só com a busca automática ligada: diz ao modelo que os resultados já estão no
# histórico, para ele usá-los em vez de pedir o documento ou buscar de novo à toa.
_AUTO_SEARCH_RULE = (
    "\n\n---\n"
    "**Busca automática:** antes desta resposta o sistema já buscou, com a mensagem do "
    "cliente, no documento que ele enviou (se houver) e na sua base de conhecimento (se "
    "houver arquivos). Os resultados estão no histórico, como respostas dessas ferramentas. "
    "Use o que for relevante e ignore os trechos sem relação com a pergunta. Se não "
    "bastarem, busque de novo com outra pergunta antes de dizer que não encontrou."
)


def _searched_this_turn(messages: list, tool_name: str) -> bool:
    """O agente atual já rodou a ferramenta neste turno.

    Olha só o que veio depois da última mensagem do contato e da última
    transferência: uma busca da recepção antes de transferir não conta como a do
    especialista, que precisa buscar ele mesmo para dar o parecer.
    """
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return False
        if isinstance(message, ToolMessage):
            if message.name == tool_name:
                return True
            if message.name == "transfer_to_agent":
                return False
    return False


def _log_final_sources(via_tool: bool, reference_ids, evidence: dict) -> None:
    """Registra como a resposta final citou fontes — só contagens, nunca conteúdo.

    Distingue o modelo que ignora responder_com_fontes (via_ferramenta=nao) do que
    a usa sem citar (ids_enviados=0) ou com IDs inválidos (ids_validos < ids_enviados).
    """
    sent = len(reference_ids) if isinstance(reference_ids, list) else 0
    logger.info(
        "Resposta final | via_ferramenta={} | ids_enviados={} | ids_validos={} | status={}",
        "sim" if via_tool else "nao",
        sent,
        len(evidence["sources"]),
        evidence["status"],
    )


# Tools cujo conversation_id vem SEMPRE do estado do grafo, nunca do LLM —
# o tenant_id vive dentro dele (isolamento multi-tenant). As tools de
# documento (ver agents/tools.py) usam conversation_id só pra log/rastreio,
# mas mesmo assim nunca confiam no valor que o LLM eventualmente informe.
STATE_SCOPED_TOOLS = {
    "bucar_base_conhecimento_usuario",
    "buscar_base_conhecimento_agente",
} | DOCUMENT_TOOLS


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

    auto = auto_search.enabled()
    if auto:
        calls = auto_search.plan(state["messages"], current)
        if calls:
            logger.info(
                "Busca automática | agent_id={} | ferramentas={}",
                current["id"],
                ",".join(c["name"] for c in calls),
            )
            # Sem chamar o modelo: o tool_node executa as buscas e o agent_node
            # volta já com os resultados. A flag de primeira resposta fica para a
            # chamada seguinte, que é a que responde.
            return Command(
                update={
                    "messages": [auto_search.search_message(calls)],
                    "current_agent_id": current["id"],
                },
                goto="tool_node",
            )

    last_messages = strip_messages(
        auto_search.drop_previous_turns(state["messages"]), state["num_before_messages"]
    )

    tools_for_agent = [
        transfer_to_agent,
        sinalizar_urgencia,
        responder_com_fontes,
        buscar_base_conhecimento_agente,
        bucar_base_conhecimento_usuario,
    ]
    if not is_entry_point:
        # Ponto de entrada (secretária, por padrão) só faz triagem/transferência
        # — gerar documento fica com os especialistas, por enquanto. Decisão
        # deliberada, não uma limitação técnica; revisitar se algum tenant
        # precisar do ponto de entrada gerando documento direto.
        tools_for_agent += [
            fazer_contrato,
            fazer_multa,
            fazer_advertencia,
            fazer_oficio,
            enviar_edital_convocacao,
            enviar_aviso,
        ]
    # Na chegada de um especialista com base própria, a busca é obrigatória antes
    # do parecer: só a instrução não bastava (o gpt-5-mini respondia "me manda o
    # contrato" sem buscar, mesmo com o documento na base — produção, 2026-11-05).
    # source_searches é zerado a cada mensagem do cliente (services/call_agent.py),
    # então "ainda não buscou" vale para o turno atual.
    #
    # O documento que o próprio contato enviou fica na base pessoal da conversa,
    # buscada por outra ferramenta — e o especialista também não a consultava:
    # pedia de novo o PDF que já estava na conversa (experimento de anexos,
    # 2026-11-06: 46 de 175 respostas). Ele vem primeiro: a pergunta costuma ser
    # sobre o documento que acabou de chegar. Depois dele, a base do escritório.
    messages = state["messages"]
    # Com a busca automática ligada, as buscas já aconteceram acima; a forçada
    # fica só para AGENTS_AUTO_SEARCH=false.
    force_contact_search = (
        not auto
        and is_first_run
        and _contact_sent_document(messages)
        and not _searched_this_turn(messages, "bucar_base_conhecimento_usuario")
    )
    force_kb_search = (
        not auto
        and is_first_run
        and not force_contact_search
        and bool(current.get("knowledge_base_file_ids"))
        and not state.get("source_searches", {}).get(current["id"])
        and not _searched_this_turn(messages, "buscar_base_conhecimento_agente")
    )
    if force_contact_search:
        logger.info(
            "Busca no documento do contato forçada na chegada do especialista | agent_id={}",
            current["id"],
        )
        model_with_tools = model.bind_tools(
            tools_for_agent, tool_choice="bucar_base_conhecimento_usuario"
        )
    elif force_kb_search:
        logger.info("Busca na base forçada na chegada do especialista | agent_id={}", current["id"])
        model_with_tools = model.bind_tools(
            tools_for_agent, tool_choice="buscar_base_conhecimento_agente"
        )
    else:
        model_with_tools = model.bind_tools(tools_for_agent)

    # Urgência no topo: no fim do prompt o modelo a ignorava ao transferir.
    prompt = (
        _urgency_rule()
        + current["instructions"]
        + _CONTINUITY_RULE
        + _KNOWLEDGE_BASE_RULE
        + _ATTACHMENT_RULE
        + (_AUTO_SEARCH_RULE if auto else "")
        + SOURCE_RULE
    )
    other_agents = [a for a in state.get("agents", []) if a["id"] != current["id"]]
    if other_agents:
        roster_text = "\n".join(f"- agent_id: {a['id']} — {a['name']}" for a in other_agents)
        prompt += (
            "\n\n---\n"
            "**Agentes disponíveis para transferência** (use o agent_id exato ao "
            "chamar transfer_to_agent — nunca invente ou abrevie o id):\n"
            f"{roster_text}"
        )

    if is_first_run:
        prompt += (
            "\n\n---\n"
            "**Instrução:** Esta é sua primeira resposta neste atendimento. "
            "### Se Apresente, diga sua especialidade e diga que dali para frente é "
            "responsável pelo atendimento. "
            "Leia o histórico completo e responda diretamente com seu parecer sobre o caso. "
            "Se a pergunta depende de um documento (contrato, cláusula, prazo, valor, nome), "
            "consulte a base de conhecimento antes de dar o parecer — não peça o documento ao "
            "cliente sem buscar primeiro. Se o cliente enviou um documento nesta conversa, ele "
            "já está disponível em bucar_base_conhecimento_usuario: use o que a busca trouxer "
            "e nunca peça de novo um arquivo que ele já mandou. "
        )

    response = await model_with_tools.ainvoke(
        [
            SystemMessage(content=prompt),
            *last_messages,
        ]
    )

    final_calls = [c for c in response.tool_calls if c["name"] == "responder_com_fontes"]
    if final_calls:
        response = response.model_copy(update={"content": ""})
    if final_calls and len(response.tool_calls) == 1:
        args = final_calls[0]["args"]
        answer = args.get("answer")
        if isinstance(answer, str) and answer.strip():
            reference_ids = args.get("reference_ids", [])
            evidence = response_evidence(state, current, reference_ids)
            _log_final_sources(True, reference_ids, evidence)
            # Preserve usage and identity, but never persist internal tool arguments in
            # the customer-facing message or leave an unanswered tool call in memory.
            response = response.model_copy(
                update={
                    "content": answer,
                    "tool_calls": [],
                    "invalid_tool_calls": [],
                    "additional_kwargs": {"response_sources": evidence},
                }
            )
    if not final_calls:
        evidence = response_evidence(state, current)
        response.additional_kwargs["response_sources"] = evidence
        if not response.tool_calls:
            _log_final_sources(False, [], evidence)

    update: dict = {"messages": [response], "current_agent_id": current["id"]}
    if is_first_run and not (force_contact_search or force_kb_search):
        # Com a busca forçada, a "primeira resposta" (apresentação + parecer) é a
        # chamada seguinte, já com o resultado da busca no histórico.
        update["receptive_message_specialist"] = False

    if response.tool_calls:
        tool_name = response.tool_calls[0]["name"]
        logger.info("Ferramenta selecionada | tool={}", tool_name)

        if tool_name == "transfer_to_agent" and not response.content:
            target_id = response.tool_calls[0]["args"].get("agent_id")
            target = agents_by_id.get(target_id)
            label = target["name"] if target else "outro agente"
            farewell = f"um momento... vou te passar pra(o) {label} agora."
            response = response.model_copy(update={"content": farewell})
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

    prepared = []
    for tool_call in tool_calls:
        if tool_call["name"] == "responder_com_fontes":
            prepared.append((tool_call, None, None))
            continue
        tool = tools_by_name.get(tool_call["name"])

        if tool is None:
            logger.warning("Ferramenta não encontrada | tool={}", tool_call["name"])
            continue

        args = dict(tool_call["args"])
        if tool_call["name"] in STATE_SCOPED_TOOLS:
            args["conversation_id"] = state["conversation_id"]
        if tool_call["name"] == "bucar_base_conhecimento_usuario":
            args["has_contact_documents"] = _contact_sent_document(state["messages"])
        if tool_call["name"] == "buscar_base_conhecimento_agente":
            current = agents_by_id.get(state.get("current_agent_id"))
            args["knowledge_base_file_ids"] = (current or {}).get("knowledge_base_file_ids", [])
            args["reference_file_ids"] = (current or {}).get("reference_file_ids", [])
        if tool_call["name"] == "transfer_to_agent":
            args["valid_agent_ids"] = list(agents_by_id.keys())

        logger.info("Executando ferramenta | tool={} | argumentos={}", tool_call["name"], len(args))
        prepared.append((tool_call, tool, args))

    observations = iter(
        await asyncio.gather(*(tool.ainvoke(args) for _, tool, args in prepared if tool))
    )

    for tool_call, tool, _args in prepared:
        if tool is None:
            messages.append(
                ToolMessage(
                    content="Finalize chamando responder_com_fontes sozinha, com answer não vazio.",
                    tool_call_id=tool_call["id"],
                )
            )
            continue
        observation = next(observations)
        logger.info("Ferramenta concluída | tool={}", tool_call["name"])

        if tool_call["name"] == "buscar_base_conhecimento_agente":
            current = agents_by_id.get(state.get("current_agent_id"))
            if current:
                observation, evidence_updates = collect_search(
                    observation, {**state, **state_updates}, current
                )
                state_updates.update(evidence_updates)

        if isinstance(observation, Command):
            if observation.update:
                logger.info(
                    "Atualizando estado via Command | updates={}",
                    list(observation.update.keys()),
                )
                state_updates.update(observation.update)
            content = (
                "Conversa sinalizada como urgente para a equipe. Continue o atendimento."
                if tool_call["name"] == "sinalizar_urgencia"
                else ""
            )
        else:
            content = str(observation)

        messages.append(
            ToolMessage(content=content, tool_call_id=tool_call["id"], name=tool_call["name"])
        )

    logger.info("tool_node finalizado | mensagens_geradas={}", len(messages))
    return {"messages": messages, **state_updates}
