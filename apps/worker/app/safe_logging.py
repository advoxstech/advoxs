"""Helpers para manter dados sensíveis fora dos logs."""

import hashlib
import uuid
from urllib.parse import urlsplit


def safe_identifier(value: object) -> str:
    """Preserva UUIDs internos; outros identificadores viram impressão digital curta."""
    raw = str(value)
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        return f"ref:{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def safe_error(error: BaseException) -> str:
    """O tipo basta para diagnóstico sem copiar URL, payload ou credencial da exceção."""
    return type(error).__name__


def safe_url(value: str) -> str:
    """Mantém apenas esquema e host; path/query podem carregar tokens ou nomes."""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = f":{parsed.port}" if parsed.port else ""
    except (TypeError, ValueError):
        return "[url-redigida]"
    if not parsed.scheme or not hostname:
        return "[url-redigida]"
    return f"{parsed.scheme}://{hostname}{port}/[redigido]"
