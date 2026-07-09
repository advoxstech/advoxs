import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_session
from app.core.redis import get_redis
from app.core.security import hash_password
from app.main import app
from app.models import PlatformAdmin
from app.services.platform_admin_auth import BLACKLIST_PREFIX

ADMIN_ID = uuid.uuid4()
PASSWORD = "senha-secreta"


def _admin() -> MagicMock:
    admin = MagicMock(spec=PlatformAdmin)
    admin.id = ADMIN_ID
    admin.role = "superadmin"
    admin.password_hash = hash_password(PASSWORD)
    return admin


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture
def redis():
    mock = AsyncMock()
    mock.exists.return_value = 0
    return mock


@pytest.fixture
def client(session, redis):
    async def override_session():
        yield session

    async def override_redis():
        return redis

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_redis] = override_redis
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestLogin:
    def test_login_valido_retorna_par_de_tokens(self, client, session) -> None:
        session.scalar.return_value = _admin()

        response = client.post(
            "/api/v1/platform-admin/auth/login", json={"email": "a@b.com", "password": PASSWORD}
        )

        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body and "refresh_token" in body

    def test_senha_incorreta_retorna_401(self, client, session) -> None:
        session.scalar.return_value = _admin()

        response = client.post(
            "/api/v1/platform-admin/auth/login", json={"email": "a@b.com", "password": "errada"}
        )

        assert response.status_code == 401

    def test_email_inexistente_retorna_401(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post(
            "/api/v1/platform-admin/auth/login", json={"email": "x@y.com", "password": PASSWORD}
        )

        assert response.status_code == 401


class TestRefresh:
    def test_refresh_valido_rotaciona_e_blacklista_o_antigo(self, client, session, redis) -> None:
        session.scalar.return_value = _admin()
        session.get.return_value = _admin()
        login = client.post(
            "/api/v1/platform-admin/auth/login", json={"email": "a@b.com", "password": PASSWORD}
        )
        old_refresh = login.json()["refresh_token"]

        response = client.post(
            "/api/v1/platform-admin/auth/refresh", json={"refresh_token": old_refresh}
        )

        assert response.status_code == 200
        redis.set.assert_awaited_once()
        key = redis.set.await_args.args[0]
        assert key.startswith(BLACKLIST_PREFIX)

    def test_refresh_blacklistado_retorna_401(self, client, redis) -> None:
        redis.exists.return_value = 1
        from app.core.platform_security import create_platform_refresh_token

        token = create_platform_refresh_token(str(ADMIN_ID))

        response = client.post("/api/v1/platform-admin/auth/refresh", json={"refresh_token": token})

        assert response.status_code == 401


class TestLogout:
    def test_logout_blacklista_o_refresh(self, client, redis) -> None:
        from app.core.platform_security import create_platform_refresh_token

        token = create_platform_refresh_token(str(ADMIN_ID))

        response = client.post("/api/v1/platform-admin/auth/logout", json={"refresh_token": token})

        assert response.status_code == 204
        redis.set.assert_awaited_once()
