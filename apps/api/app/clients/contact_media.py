"""Download on demand for human attendants; no RAG or AI processing."""

from urllib.parse import urlsplit

import httpx

from app.core.config import settings

MAX_MEDIA_BYTES = 20 * 1024 * 1024


class ContactMediaError(Exception):
    pass


class ContactMediaTooLarge(ContactMediaError):
    pass


def trusted_url(url: str, domains: tuple[str, ...]) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and parsed.port in (None, 443)
        and any(host == domain or host.endswith("." + domain) for domain in domains)
    )


async def download_contact_media(ref: str, provider: str, token: str) -> bytes:
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            if provider == "meta":
                if not ref.isdigit():
                    raise ContactMediaError("Identificador de mídia inválido")
                headers = {"Authorization": f"Bearer {token}"}
                response = await client.get(
                    f"{settings.graph_api_base_url}/{settings.graph_api_version}/{ref}",
                    headers=headers,
                )
                response.raise_for_status()
                url = response.json().get("url", "")
                if not trusted_url(url, ("fbsbx.com", "fbcdn.net", "whatsapp.net")):
                    raise ContactMediaError("Origem da mídia não permitida")
            else:
                url = ref
                if not trusted_url(url, ("z-api.io", "storage.googleapis.com")):
                    raise ContactMediaError("Origem da mídia não permitida")
                # Never disclose provider credentials to third-party storage.
                headers = (
                    {"Client-Token": token} if token and trusted_url(url, ("z-api.io",)) else {}
                )
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                if 300 <= response.status_code < 400:
                    raise ContactMediaError("Redirecionamento de mídia não permitido")
                length = response.headers.get("content-length", "")
                if length.isdigit() and int(length) > MAX_MEDIA_BYTES:
                    raise ContactMediaTooLarge()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > MAX_MEDIA_BYTES:
                        raise ContactMediaTooLarge()
                    data.extend(chunk)
                return bytes(data)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise ContactMediaError("Mídia indisponível no provedor") from exc
