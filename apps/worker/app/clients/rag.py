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
) -> None:
    """Envia o arquivo ao api_rag. doc_id = id de knowledge_base_files.

    Levanta httpx.HTTPStatusError em resposta de erro (raise_for_status).
    """
    response = await http.post(
        "/documents/users/insert",
        data={"tenant_id": tenant_id, "conversation_id": KB_CONVERSATION_ID, "doc_id": doc_id},
        files={"file": (filename, file_bytes, "application/octet-stream")},
        headers={"Authorization": settings.rag_api_key},
    )
    response.raise_for_status()
