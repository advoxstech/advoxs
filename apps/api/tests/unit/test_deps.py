import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import get_current_tenant
from app.core.security import create_access_token, create_refresh_token

USER_ID = str(uuid.uuid4())
TENANT_ID = str(uuid.uuid4())


def _credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def test_token_valido_injeta_contexto() -> None:
    token = create_access_token(USER_ID, TENANT_ID, "admin")
    session = AsyncMock()
    session.get.return_value = type("UserStub", (), {"session_version": 0})()

    ctx = await get_current_tenant(_credentials(token), session)

    assert str(ctx.user_id) == USER_ID
    assert str(ctx.tenant_id) == TENANT_ID
    assert ctx.role == "admin"


async def test_sem_credenciais_retorna_401() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_current_tenant(None, AsyncMock())

    assert exc.value.status_code == 401


async def test_token_invalido_retorna_401() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_current_tenant(_credentials("lixo"), AsyncMock())

    assert exc.value.status_code == 401


async def test_refresh_token_nao_autentica() -> None:
    token = create_refresh_token(USER_ID)

    with pytest.raises(HTTPException):
        await get_current_tenant(_credentials(token), AsyncMock())


async def test_versao_antiga_da_sessao_retorna_401() -> None:
    token = create_access_token(USER_ID, TENANT_ID, "admin", session_version=1)
    session = AsyncMock()
    session.get.return_value = type("UserStub", (), {"session_version": 2})()

    with pytest.raises(HTTPException) as exc:
        await get_current_tenant(_credentials(token), session)

    assert exc.value.status_code == 401

    assert exc.value.status_code == 401
