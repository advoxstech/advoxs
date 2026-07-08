"""Client do api_rag (serviço interno, API key única — nunca exposto ao escritório)."""

import httpx

from app.core.config import settings


class RagApiError(Exception):
    """Falha de comunicação ou resposta de erro do api_rag."""


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
