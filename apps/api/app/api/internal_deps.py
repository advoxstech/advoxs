"""Auth de serviço interno: agents -> api (direção oposta de AGENTS_API_KEY,
que autentica o api/worker chamando o agents)."""

import secrets

from fastapi import Header, HTTPException, status

from app.core.config import settings


async def verify_internal_service_key(authorization: str | None = Header(default=None)) -> None:
    if not settings.internal_service_key:
        return
    if not authorization or not secrets.compare_digest(
        authorization, settings.internal_service_key
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Key inválida ou ausente",
        )
