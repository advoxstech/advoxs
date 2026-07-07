async def process_inbound_message(
    ctx: dict, tenant_id: str, conversation_id: str, message_id: str
) -> None:
    """Verifica o estado da conversa (agent|human) e repassa para o agents service."""
