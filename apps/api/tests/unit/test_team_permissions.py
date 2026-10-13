import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.core.security import create_access_token
from app.main import app


@pytest.mark.parametrize(
    "path",
    [
        "/team",
        "/team/invitations",
        "/team/notifications",
        "/agents",
        "/billing/balance",
        "/dashboard",
        "/knowledge-base/files",
        "/onboarding",
        "/whatsapp/connection",
    ],
)
def test_lawyer_cannot_access_admin_endpoints(path):
    context = TenantContext(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role="lawyer")
    app.dependency_overrides[get_current_tenant] = lambda: context
    app.dependency_overrides[get_tenant_session] = lambda: AsyncMock()
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1" + path)
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


async def test_disabled_user_rejected_and_database_role_overrides_old_token():
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    user = SimpleNamespace(
        id=user_id, tenant_id=tenant_id, name="Ana", role="lawyer", active=False, session_version=0
    )
    session = AsyncMock()
    session.get.return_value = user
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=create_access_token(str(user_id), str(tenant_id), "admin", 0)
    )
    with pytest.raises(HTTPException) as error:
        await get_current_tenant(credentials, session)
    assert error.value.status_code == 401
    user.active = True
    assert (await get_current_tenant(credentials, session)).role == "lawyer"
