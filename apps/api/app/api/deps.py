"""Dependencies compartilhadas das rotas autenticadas."""

import uuid
from collections.abc import AsyncIterator

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session, get_system_session
from app.core.platform_security import decode_platform_token
from app.core.security import decode_token
from app.models import OfficeMembership, OfficeSession, Tenant, User

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
    name: str = "Equipe"


async def get_current_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_system_session),
):
    if credentials is None:
        raise _NAO_AUTENTICADO
    try:
        payload = decode_token(credentials.credentials)
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise _NAO_AUTENTICADO
    if payload.get("type") != "access":
        raise _NAO_AUTENTICADO
    user = await session.get(User, user_id)
    if user is None or not user.active or payload.get("session_version", 0) != user.session_version:
        raise _NAO_AUTENTICADO
    if payload.get("sid"):
        try:
            office_session = await session.get(OfficeSession, uuid.UUID(payload["sid"]))
        except ValueError:
            raise _NAO_AUTENTICADO
        if office_session is None or office_session.user_id != user.id:
            raise _NAO_AUTENTICADO
    return user, payload


async def get_current_tenant(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_system_session),
    expected_office: str | None = Header(default=None, alias="X-Office-ID"),
) -> TenantContext:
    user, payload = await get_current_identity(credentials, session)
    if payload.get("sid"):
        office_session = await session.get(OfficeSession, uuid.UUID(payload["sid"]))
        if payload.get("context_version", 0) != office_session.version or payload.get(
            "tenant_id"
        ) != str(office_session.tenant_id or ""):
            raise HTTPException(409, "office_context_changed")
    try:
        tenant_id = uuid.UUID(payload["tenant_id"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, "office_selection_required")
    if isinstance(expected_office, str) and expected_office != str(tenant_id):
        raise HTTPException(409, "office_context_changed")
    membership = await session.get(OfficeMembership, (tenant_id, user.id))
    tenant = await session.get(Tenant, tenant_id)
    if not membership or not membership.active or not tenant or tenant.status != "active":
        raise HTTPException(409, "office_selection_required")
    return TenantContext(user_id=user.id, tenant_id=tenant_id, role=membership.role, name=user.name)


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
