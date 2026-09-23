import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_system_session
from app.core.redis import get_redis
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password
from app.main import app
from app.models import Tenant, User
from app.services import auth as auth_service_module
from app.services.auth import BLACKLIST_PREFIX

USER_ID = uuid.uuid4()
TENANT_ID = uuid.uuid4()
PASSWORD = "senha-secreta"


def _user():
    user = MagicMock(spec=User)
    user.id = USER_ID
    user.tenant_id = TENANT_ID
    user.role = "admin"
    user.password_hash = hash_password(PASSWORD)
    user.session_version = 0
    return user


def _tenant(status: str = "active"):
    tenant = MagicMock(spec=Tenant)
    tenant.id = TENANT_ID
    tenant.status = status
    return tenant


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture
def redis():
    mock = AsyncMock()
    mock.get.return_value = None
    mock.set.return_value = True
    mock.incr.return_value = 1
    mock.ttl.return_value = 900
    return mock


@pytest.fixture
def client(session, redis):
    async def override_session():
        yield session

    async def override_redis():
        return redis

    app.dependency_overrides[get_system_session] = override_session
    app.dependency_overrides[get_redis] = override_redis
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestLogin:
    def test_login_valido_retorna_par_de_tokens(self, client, session) -> None:
        session.scalar.return_value = _user()
        session.get.return_value = _tenant()

        response = client.post(
            "/api/v1/auth/login", json={"email": "a@b.com", "password": PASSWORD}
        )

        assert response.status_code == 200
        body = response.json()
        access = decode_token(body["access_token"])
        assert access["type"] == "access"
        assert access["tenant_id"] == str(TENANT_ID)
        assert decode_token(body["refresh_token"])["type"] == "refresh"

    def test_senha_errada_retorna_401(self, client, session) -> None:
        session.scalar.return_value = _user()

        response = client.post(
            "/api/v1/auth/login", json={"email": "a@b.com", "password": "errada"}
        )

        assert response.status_code == 401

    def test_limite_de_tentativas_retorna_429(self, client, redis) -> None:
        redis.get.side_effect = ["5", None]

        response = client.post(
            "/api/v1/auth/login", json={"email": "a@b.com", "password": "errada"}
        )

        assert response.status_code == 429
        assert response.headers["retry-after"] == "900"

    def test_email_desconhecido_retorna_401(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post(
            "/api/v1/auth/login", json={"email": "x@y.com", "password": PASSWORD}
        )

        assert response.status_code == 401

    def test_tenant_suspenso_retorna_403(self, client, session) -> None:
        session.scalar.return_value = _user()
        session.get.return_value = _tenant(status="suspended")

        response = client.post(
            "/api/v1/auth/login", json={"email": "a@b.com", "password": PASSWORD}
        )

        assert response.status_code == 403


class TestRefresh:
    def test_rotacao_revoga_jti_antigo_e_emite_novo_par(self, client, session, redis) -> None:
        session.get.side_effect = [_user(), _tenant()]
        old_token = create_refresh_token(str(USER_ID))
        old_jti = decode_token(old_token)["jti"]

        response = client.post("/api/v1/auth/refresh", json={"refresh_token": old_token})

        assert response.status_code == 200
        body = response.json()
        assert decode_token(body["refresh_token"])["jti"] != old_jti
        blacklist_key = redis.set.await_args_list[0].args[0]
        assert blacklist_key == f"{BLACKLIST_PREFIX}{old_jti}"

    def test_refresh_revogado_retorna_401(self, client, redis) -> None:
        redis.set.return_value = False
        redis.get.return_value = None
        token = create_refresh_token(str(USER_ID))

        response = client.post("/api/v1/auth/refresh", json={"refresh_token": token})

        assert response.status_code == 401

    def test_refresh_de_versao_anterior_retorna_401(self, client, session) -> None:
        user = _user()
        user.session_version = 2
        session.get.return_value = user
        token = create_refresh_token(str(USER_ID), session_version=1)

        response = client.post("/api/v1/auth/refresh", json={"refresh_token": token})

        assert response.status_code == 401
        assert response.json()["detail"] == "Sessão revogada"

    def test_access_token_no_lugar_de_refresh_retorna_401(self, client) -> None:
        token = create_access_token(str(USER_ID), str(TENANT_ID), "admin")

        response = client.post("/api/v1/auth/refresh", json={"refresh_token": token})

        assert response.status_code == 401

    def test_token_invalido_retorna_401(self, client) -> None:
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": "lixo"})

        assert response.status_code == 401

    async def test_duas_renovacoes_simultaneas_recebem_o_mesmo_novo_par(self) -> None:
        class MemoryRedis:
            def __init__(self):
                self.values = {}
                self.lock = asyncio.Lock()

            async def set(self, key, value, *, ex=None, nx=False):
                async with self.lock:
                    if nx and key in self.values:
                        return False
                    self.values[key] = value
                    return True

            async def get(self, key):
                return self.values.get(key)

        user = _user()
        tenant = _tenant()
        session = AsyncMock()

        async def get(model, _identifier):
            return user if model is User else tenant

        session.get = get
        redis = MemoryRedis()
        token = create_refresh_token(str(USER_ID), user.session_version)

        first, second = await asyncio.gather(
            auth_service_module.refresh(token, session, redis),
            auth_service_module.refresh(token, session, redis),
        )

        assert first == second


class TestLogout:
    def test_logout_revoga_refresh(self, client, redis) -> None:
        token = create_refresh_token(str(USER_ID))
        jti = decode_token(token)["jti"]

        response = client.post("/api/v1/auth/logout", json={"refresh_token": token})

        assert response.status_code == 204
        assert redis.set.await_args.args[0] == f"{BLACKLIST_PREFIX}{jti}"


class TestSignupLogin:
    def test_token_valido_retorna_par_de_tokens(self, client, session, monkeypatch) -> None:
        user = _user()
        session.get.return_value = user
        consume = AsyncMock(return_value=str(user.id))
        monkeypatch.setattr(auth_service_module, "consume_login_token", consume)
        monkeypatch.setattr(
            auth_service_module, "_validar_tenant_ativo", AsyncMock(return_value=None)
        )

        response = client.post("/api/v1/auth/signup-login", json={"token": "tok-valido"})

        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body and "refresh_token" in body

    def test_token_invalido_retorna_401(self, client, session, monkeypatch) -> None:
        monkeypatch.setattr(
            auth_service_module, "consume_login_token", AsyncMock(return_value=None)
        )

        response = client.post("/api/v1/auth/signup-login", json={"token": "tok-ruim"})

        assert response.status_code == 401
        assert response.json()["detail"] == "Token inválido ou expirado"

    def test_user_sumiu_retorna_401_generico(self, client, session, monkeypatch) -> None:
        monkeypatch.setattr(
            auth_service_module,
            "consume_login_token",
            AsyncMock(return_value=str(uuid.uuid4())),
        )
        session.get.return_value = None

        response = client.post("/api/v1/auth/signup-login", json={"token": "tok"})

        assert response.status_code == 401
