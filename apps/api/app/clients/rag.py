"""Client do api_rag (serviço interno, API key única — nunca exposto ao escritório)."""

import httpx

from app.core.config import settings


class RagApiError(Exception):
    """Falha de comunicação ou resposta de erro do api_rag."""


async def read_office_document(tenant_id: str, document_id: str) -> bytes | None:
    """Bounded authenticated download; None means the original no longer exists."""
    try:
        async with httpx.AsyncClient(base_url=settings.rag_api_url, timeout=30) as client:
            async with client.stream(
                "GET",
                f"/documents/users/{document_id}/content",
                params={"tenant_id": tenant_id},
                headers={"Authorization": settings.rag_api_key},
            ) as response:
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > settings.kb_max_file_size_bytes:
                        raise RagApiError("Documento excede o limite de download")
                    data.extend(chunk)
                return bytes(data)
    except httpx.HTTPError as exc:
        raise RagApiError("Falha ao consultar o documento original") from exc


async def insert_user_document(
    *,
    tenant_id: str,
    conversation_id: str,
    doc_id: str,
    filename: str,
    file_bytes: bytes,
) -> None:
    """Ingere um documento na base pessoal de um contato. `conversation_id`
    precisa ser só o contact_phone_number — NUNCA o thread_id composto
    "{tenant_id}:{contact_phone_number}" do checkpoint do agents, que
    `clients/retrieval.py::retrieval_usuario` (apps/agents) divide em
    tenant_id+contato antes de consultar o api_rag; passar o composto aqui
    grava o documento sob uma chave que a busca nunca vai encontrar. Usado
    pelo anexo de conversa de TESTE (app/services/test_attachments.py), que
    roda síncrono aqui no `api` em vez de assíncrono no `worker` (ver
    apps/worker/app/tasks/attachments.py, o caminho equivalente pra
    conversas reais via WhatsApp)."""
    try:
        async with httpx.AsyncClient(base_url=settings.rag_api_url, timeout=60) as client:
            response = await client.post(
                "/documents/users/insert",
                data={
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                    "doc_id": doc_id,
                },
                files={"file": (filename, file_bytes, "application/octet-stream")},
                headers={"Authorization": settings.rag_api_key},
            )
    except httpx.HTTPError as exc:
        raise RagApiError(f"api_rag indisponível: {exc}") from exc
    if response.status_code >= 400:
        raise RagApiError(f"api_rag retornou HTTP {response.status_code}")


async def delete_documents(tenant_id: str, doc_ids: list[str]) -> None:
    """Remove documentos no api_rag (disco + Qdrant + Postgres de lá).

    Idempotente do lado do api_rag: ids inexistentes são ignorados.
    """
    try:
        async with httpx.AsyncClient(base_url=settings.rag_api_url, timeout=30) as client:
            response = await client.delete(
                "/documents/users/delete",
                params={"tenant_id": tenant_id, "docs_ids": doc_ids},
                headers={"Authorization": settings.rag_api_key},
            )
    except httpx.HTTPError as exc:
        raise RagApiError(f"api_rag indisponível: {exc}") from exc
    if response.status_code != 200:
        raise RagApiError(f"api_rag retornou HTTP {response.status_code}")
