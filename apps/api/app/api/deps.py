"""Dependencies compartilhadas das rotas autenticadas."""

import uuid
from collections.abc import AsyncIterator

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.platform_security import decode_platform_token
from app.core.security import decode_token

_bearer = HTTPBearer(auto_error=False)

_NAO_AUTENTICADO = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Não autenticado",
    headers={"WWW-Authenticate": "Bearer"},
)


class TenantContext(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str


async def get_current_tenant(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> TenantContext:
    """Decodifica o JWT de acesso e injeta user_id/tenant_id/role no contexto."""
    if credentials is None:
        raise _NAO_AUTENTICADO
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise _NAO_AUTENTICADO
    if payload.get("type") != "access":
        raise _NAO_AUTENTICADO

    return TenantContext(
        user_id=payload["sub"],
        tenant_id=payload["tenant_id"],
        role=payload["role"],
    )


def _tenant_id_setter(tenant_id: str):
    """Listener de after_begin: seta app.tenant_id no início de cada transação."""

    def _set(sync_session, transaction, connection) -> None:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )

    return _set


async def get_tenant_session(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_session),
) -> AsyncIterator[AsyncSession]:
    """Sessão com app.tenant_id setado — ativa as policies de RLS (defesa em
    profundidade, além do filtro por tenant_id na aplicação).

    set_config com is_local=true vale por transação, e rotas commitam no meio
    da request (commit → refresh abre transação nova) — por isso a variável é
    setada via listener de after_begin, no início de CADA transação da sessão,
    e não uma única vez: qualquer query rodando com app.tenant_id vazio faria
    a policy de RLS estourar com `invalid input syntax for type uuid: ""`.
    O escopo por transação continua valendo — nada vaza para outras requests
    via pool de conexões.
    """
    setter = _tenant_id_setter(str(ctx.tenant_id))
    event.listen(session.sync_session, "after_begin", setter)
    try:
        yield session
    finally:
        event.remove(session.sync_session, "after_begin", setter)


class PlatformAdminContext(BaseModel):
    admin_id: uuid.UUID
    role: str


async def get_current_platform_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> PlatformAdminContext:
    """Decodifica o JWT do platform_admin — secret separado do de tenant."""
    if credentials is None:
        raise _NAO_AUTENTICADO
    try:
        payload = decode_platform_token(credentials.credentials)
    except jwt.PyJWTError:
        raise _NAO_AUTENTICADO
    if payload.get("type") != "platform_access":
        raise _NAO_AUTENTICADO

    return PlatformAdminContext(admin_id=payload["sub"], role=payload["role"])
