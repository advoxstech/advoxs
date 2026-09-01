"""Client do api_rag — ingestão de documentos da base de conhecimento."""

import httpx

from app.config import settings

# conversation_id reservado da base de conhecimento do escritório (espelha
# o tenant reservado "system" da base da plataforma).
KB_CONVERSATION_ID = "kb"


async def ingest_document(
    http: httpx.AsyncClient,
    *,
    tenant_id: str,
    doc_id: str,
    filename: str,
    file_bytes: bytes,
    conversation_id: str = KB_CONVERSATION_ID,
) -> None:
    """Envia o arquivo ao api_rag. doc_id = id de knowledge_base_files (base
    do escritório, conversation_id="kb" default) ou o id da própria mensagem
    (anexo do contato, conversation_id=thread_id — ver app/tasks/attachments.py).

    Levanta httpx.HTTPStatusError em resposta de erro (raise_for_status).
    """
    response = await http.post(
        "/documents/users/insert",
        data={"tenant_id": tenant_id, "conversation_id": conversation_id, "doc_id": doc_id},
        files={"file": (filename, file_bytes, "application/octet-stream")},
        headers={"Authorization": settings.rag_api_key},
    )
    response.raise_for_status()
