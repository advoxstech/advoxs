import uuid
from types import SimpleNamespace

import pytest

from app.core.security import hash_password
from app.services.profile import InvalidCurrentPasswordError, change_password, update_tenant_name

TENANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def _tenant(name: str = "Escritório Antigo") -> SimpleNamespace:
    return SimpleNamespace(id=TENANT_ID, name=name)


def _user(password: str = "senha-atual") -> SimpleNamespace:
    return SimpleNamespace(id=USER_ID, password_hash=hash_password(password))


class FakeSession:
    def __init__(self, tenant=None, user=None):
        self._tenant = tenant
        self._user = user
        self.committed = False

    async def get(self, model, id_):
        if model.__name__ == "Tenant":
            return self._tenant
        return self._user

    async def commit(self):
        self.committed = True


class TestUpdateTenantName:
    async def test_atualiza_o_nome_do_tenant(self) -> None:
        tenant = _tenant()
        session = FakeSession(tenant=tenant)

        result = await update_tenant_name(session, TENANT_ID, "Escritório Novo")

        assert result.name == "Escritório Novo"
        assert session.committed is True


class TestChangePassword:
    async def test_senha_atual_incorreta_levanta_erro(self) -> None:
        user = _user(password="senha-atual")
        session = FakeSession(user=user)

        with pytest.raises(InvalidCurrentPasswordError):
            await change_password(session, USER_ID, "senha-errada", "nova-senha-123")

        assert session.committed is False

    async def test_senha_atual_correta_atualiza_o_hash(self) -> None:
        user = _user(password="senha-atual")
        old_hash = user.password_hash
        session = FakeSession(user=user)

        await change_password(session, USER_ID, "senha-atual", "nova-senha-123")

        assert user.password_hash != old_hash
        assert session.committed is True
