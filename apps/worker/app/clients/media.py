"""Download de mídia recebida via WhatsApp (Meta Cloud API / Z-API) — usado
pra ingerir anexos do contato na base de conhecimento pessoal dele (ver
app/tasks/attachments.py). A Meta exige 2 chamadas autenticadas (media_id ->
URL assinada -> bytes); a Z-API já entrega a URL final pronta no payload do
webhook (ver comentário em InboundZApiMessage, apps/api/app/schemas/whatsapp.py),
sem necessidade de autenticação nessa segunda chamada."""

import httpx

from app.config import settings


class MediaDownloadError(Exception):
    pass


async def download_meta_media(media_id: str, access_token: str) -> bytes:
    """media_id -> GET /{media_id} (devolve uma URL assinada de curta duração)
    -> GET nessa URL. As duas chamadas exigem o mesmo Bearer token."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{settings.graph_api_base_url}/{settings.graph_api_version}/{media_id}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            meta_response = await client.get(url, headers=headers)
            meta_response.raise_for_status()
            download_url = meta_response.json().get("url")
            if not download_url:
                raise MediaDownloadError("Resposta da Graph API sem 'url' de download")
            file_response = await client.get(download_url, headers=headers)
            file_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise MediaDownloadError(f"Falha ao baixar mídia da Graph API: {exc}") from exc
    return file_response.content


async def download_zapi_media(media_url: str) -> bytes:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(media_url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise MediaDownloadError(f"Falha ao baixar mídia da Z-API: {exc}") from exc
    return response.content
