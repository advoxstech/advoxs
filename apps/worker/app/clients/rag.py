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
    timeout: float | None = None,
) -> dict:
    """Envia o arquivo ao api_rag. doc_id = id de knowledge_base_files (base
    do escritório, conversation_id="kb" default) ou o id da própria mensagem
    (anexo do contato, conversation_id=contact_phone_number — NUNCA o thread_id
    composto "{tenant_id}:{contact_phone_number}", que o agents
    (clients/retrieval.py::retrieval_usuario) divide em tenant_id+contato
    antes de consultar o api_rag; ver app/tasks/attachments.py).

    `timeout` substitui o prazo do client só nesta chamada (anexo de conversa,
    que pode ser um PDF escaneado grande).

    Levanta httpx.HTTPStatusError em resposta de erro (raise_for_status).
    Devolve o resumo da leitura (`paginas`, `ocr`, `paginas_parciais`); vazio
    quando o api_rag não o informa.
    """
    extra = {"timeout": timeout} if timeout is not None else {}
    response = await http.post(
        "/documents/users/insert",
        data={"tenant_id": tenant_id, "conversation_id": conversation_id, "doc_id": doc_id},
        files={"file": (filename, file_bytes, "application/octet-stream")},
        headers={"Authorization": settings.rag_api_key},
        **extra,
    )
    response.raise_for_status()
    return reading_summary(response)


def reading_summary(response: httpx.Response) -> dict:
    """Resumo da leitura devolvido pelo api_rag, normalizado (nunca levanta)."""
    try:
        leitura = response.json().get("leitura")
    except (ValueError, AttributeError):
        return {}
    if not isinstance(leitura, dict):
        return {}
    paginas = leitura.get("paginas")
    parciais = leitura.get("paginas_parciais")
    return {
        "pages_total": paginas if type(paginas) is int and paginas > 0 else None,
        "partial_pages": sorted(
            {p for p in parciais if type(p) is int and p > 0}
            if isinstance(parciais, list)
            else set()
        ),
    }
