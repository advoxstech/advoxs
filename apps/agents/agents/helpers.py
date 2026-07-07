from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from loguru import logger


def _placeholder_tool_message(tool_call_id: str):
    return ToolMessage(content="", tool_call_id=tool_call_id)


def strip_messages(messages, last_n):
    logger.debug("Recortando histórico de mensagens | total={} | n={}", len(messages), last_n)
    clean = []

    for m in messages:
        if m.type == "human":
            clean.append(HumanMessage(content=m.content))
        elif m.type == "ai":
            clean.append(AIMessage(content=m.content, tool_calls=m.tool_calls))
        elif m.type == "system":
            clean.append(SystemMessage(content=m.content))
        elif m.type == "tool":
            clean.append(m)

    sanitized = []
    pending_tool_ids = []

    for message in clean:
        if message.type == "ai":
            # Antes de adicionar novo AIMessage, fecha qualquer pendência anterior
            for tool_call_id in pending_tool_ids:
                sanitized.append(_placeholder_tool_message(tool_call_id))
            pending_tool_ids = []

            sanitized.append(message)
            pending_tool_ids = [tc["id"] for tc in (message.tool_calls or [])]
            continue

        if message.type == "tool":
            pending_tool_ids = [tid for tid in pending_tool_ids if tid != message.tool_call_id]
            sanitized.append(message)
            continue

        # Qualquer outra mensagem (human, system): fecha pendências ANTES de adicioná-la
        for tool_call_id in pending_tool_ids:
            sanitized.append(_placeholder_tool_message(tool_call_id))
        pending_tool_ids = []

        sanitized.append(message)

    # Fecha pendências restantes ao fim do histórico
    for tool_call_id in pending_tool_ids:
        sanitized.append(_placeholder_tool_message(tool_call_id))

    if last_n > 0:
        start_index = max(len(sanitized) - last_n, 0)
        # Não começa no meio de um bloco tool
        while start_index > 0 and sanitized[start_index].type == "tool":
            start_index -= 1
        last_messages = sanitized[start_index:]
    else:
        last_messages = []

    logger.debug("Histórico recortado | final={}", len(last_messages))
    return last_messages
