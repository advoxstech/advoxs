"""Busca automática nas bases antes de a IA responder.

A cada mensagem do cliente com conteúdo, o sistema busca no documento que ele
enviou na conversa e na base de conhecimento do agente atual, antes de chamar o
modelo. Deixar a decisão com o modelo não funcionou: ele deixava de buscar e
pedia de novo o documento (experimentos de 2026-11-05 e 2026-11-06).

As buscas viram uma chamada de ferramenta montada pelo código (sem passar pelo
modelo) e são executadas pelo tool_node como qualquer outra — com o mesmo
isolamento por escritório e o mesmo registro de fontes. `AGENTS_AUTO_SEARCH=false`
volta ao comportamento anterior (busca forçada só na chegada do especialista).
"""

import os
import re
import uuid

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

CONTACT_TOOL = "bucar_base_conhecimento_usuario"
KB_TOOL = "buscar_base_conhecimento_agente"

# Identifica as chamadas montadas aqui, para tirá-las do contexto nos turnos
# seguintes: os trechos de uma pergunta antiga só ocupariam a janela de 60
# mensagens e gastariam tokens.
AUTO_SEARCH_ID_PREFIX = "auto-search-"

# Início da nota que o worker (app/tasks/attachments.py) e as conversas de teste
# do api (app/services/test_attachments.py) anexam à mensagem do contato quando
# um documento dele foi lido e está na base pessoal da conversa — também na
# versão "processado parcialmente". Mudar o texto lá exige mudar aqui.
CONTACT_DOCUMENT_NOTE = "[Documento recebido e processado"

# Notas do sistema entre colchetes (documento, aviso de leitura, transcrição de
# áudio) saem do texto da busca; palavras incertas da transcrição, "[?palavra?]",
# ficam.
_SYSTEM_NOTE = re.compile(r"\[(?!\?)[^\]]*\]")
_MAX_QUERY_CHARS = 1500
_TRIVIAL_MAX_WORDS = 3


def enabled() -> bool:
    value = os.getenv("AGENTS_AUTO_SEARCH", "true").strip().lower()
    return value not in {"false", "0", "no", "off"}


def contact_sent_document(messages: list) -> bool:
    """O contato enviou nesta conversa algum documento que está disponível para busca."""
    return any(
        isinstance(m, HumanMessage) and CONTACT_DOCUMENT_NOTE in str(m.content) for m in messages
    )


def _is_agent_answer(message) -> bool:
    # Respostas do grafo sempre levam response_sources (agent_node); mensagens do
    # atendente sincronizadas no checkpoint (services/update_context.py) não.
    return (
        isinstance(message, AIMessage)
        and bool(message.content)
        and "response_sources" in (message.additional_kwargs or {})
    )


def turn_query(messages: list) -> str:
    """O que o cliente escreveu desde a última resposta do agente, sem as notas do sistema.

    Inclui mensagens que chegaram em rajada e o pedido feito junto com um documento
    cuja leitura demorou (gravado no checkpoint antes do turno que responde).
    """
    parts = []
    for message in reversed(messages):
        if _is_agent_answer(message):
            break
        if isinstance(message, HumanMessage):
            parts.append(str(message.content))
    text = _SYSTEM_NOTE.sub(" ", " ".join(reversed(parts)))
    return " ".join(text.split())[:_MAX_QUERY_CHARS]


def is_trivial(text: str) -> bool:
    """Mensagens como "ok", "obrigado", "bom dia": nada a buscar."""
    if "?" in text or any(c.isdigit() for c in text):
        return False
    return len(re.findall(r"\w+", text)) <= _TRIVIAL_MAX_WORDS


def _tool_ran_since(messages: list, tool_name: str, *, stop_at_transfer: bool) -> bool:
    """A ferramenta já rodou depois da última mensagem do contato.

    Com `stop_at_transfer`, só conta o que veio depois da última transferência:
    cada agente tem a sua base, e a busca da recepção não vale pelo especialista.
    """
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return False
        if isinstance(message, ToolMessage):
            if message.name == tool_name:
                return True
            if stop_at_transfer and message.name == "transfer_to_agent":
                return False
    return False


def plan(messages: list, agent: dict) -> list[dict]:
    """As buscas que ainda faltam neste turno para o agente atual (vazio = nenhuma)."""
    query = turn_query(messages)
    if not query or is_trivial(query):
        return []
    calls = []
    if contact_sent_document(messages) and not _tool_ran_since(
        messages, CONTACT_TOOL, stop_at_transfer=False
    ):
        calls.append(CONTACT_TOOL)
    if agent.get("knowledge_base_file_ids") and not _tool_ran_since(
        messages, KB_TOOL, stop_at_transfer=True
    ):
        calls.append(KB_TOOL)
    return [
        {
            "id": f"{AUTO_SEARCH_ID_PREFIX}{uuid.uuid4().hex}",
            "name": name,
            "args": {"query": query},
            "type": "tool_call",
        }
        for name in calls
    ]


def search_message(calls: list[dict]) -> AIMessage:
    """A chamada de ferramenta que o tool_node executa (conteúdo vazio: não vai ao cliente)."""
    return AIMessage(content="", tool_calls=calls)


def _is_auto_search(message) -> bool:
    if isinstance(message, ToolMessage):
        return str(message.tool_call_id).startswith(AUTO_SEARCH_ID_PREFIX)
    if isinstance(message, AIMessage) and message.tool_calls and not message.content:
        return all(
            str(c.get("id", "")).startswith(AUTO_SEARCH_ID_PREFIX) for c in message.tool_calls
        )
    return False


def drop_previous_turns(messages: list) -> list:
    """Tira do contexto as buscas automáticas de turnos anteriores; as do turno atual ficam."""
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    return [m for i, m in enumerate(messages) if i > last_human or not _is_auto_search(m)]
