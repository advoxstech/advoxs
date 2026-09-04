"""Download de mídia recebida via WhatsApp (Meta Cloud API / Z-API) — usado
pra ingerir anexos do contato na base de conhecimento pessoal dele (ver
app/tasks/attachments.py). A Meta exige 2 chamadas autenticadas (media_id ->
URL assinada -> bytes); a Z-API já entrega a URL final pronta no payload do
webhook (ver comentário em InboundZApiMessage, apps/api/app/schemas/whatsapp.py),
mas exige o mesmo header Client-Token da conta pra baixar o conteúdo — a doc
oficial de segurança da Z-API diz que o token "deve ser incluído no header de
todas as suas requisições HTTP" (developer.z-api.io/en/security/client-token),
e a ausência desse header foi identificada como causa provável de anexos via
Z-API nunca chegarem a ser ingeridos (a chamada falha silenciosamente e vira
uma nota de "falha ao baixar", best-effort, sem travar o turno)."""

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


async def download_zapi_media(media_url: str, client_token: str | None = None) -> bytes:
    headers = {"Client-Token": client_token} if client_token else {}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(media_url, headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise MediaDownloadError(f"Falha ao baixar mídia da Z-API: {exc}") from exc
    return response.content
