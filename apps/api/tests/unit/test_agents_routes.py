import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app

TENANT_ID = uuid.uuid4()
AGENT_ID = uuid.uuid4()


def _agent(name: str = "Secretária", is_entry_point: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=AGENT_ID,
        tenant_id=TENANT_ID,
        name=name,
        instructions="Você é uma secretária.",
        is_entry_point=is_entry_point,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def session():
    mock = AsyncMock()
    mock.add = MagicMock()

    async def fake_refresh(obj):
        obj.created_at = datetime.now(UTC)
        obj.updated_at = datetime.now(UTC)

    mock.refresh.side_effect = fake_refresh
    return mock


@pytest.fixture
def client(session):
    async def override_ctx():
        return TenantContext(user_id=uuid.uuid4(), tenant_id=TENANT_ID, role="admin")

    async def override_session():
        yield session

    app.dependency_overrides[get_current_tenant] = override_ctx
    app.dependency_overrides[get_tenant_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _execute_returning(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/agents")

    assert response.status_code == 401


class TestList:
    def test_lista_agentes_do_tenant(self, client, session) -> None:
        session.execute.return_value = _execute_returning([_agent()])

        response = client.get("/api/v1/agents")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["name"] == "Secretária"
        assert body[0]["is_entry_point"] is True


class TestCreate:
    def test_cria_agente(self, client, session) -> None:
        response = client.post(
            "/api/v1/agents",
            json={"name": "Vendas", "instructions": "Você vende planos.", "is_entry_point": False},
        )

        assert response.status_code == 201
        assert response.json()["name"] == "Vendas"
        session.add.assert_called_once()
        added = session.add.call_args.args[0]
        assert added.tenant_id == TENANT_ID
        session.commit.assert_awaited()

    def test_criar_como_ponto_de_entrada_desmarca_o_anterior(self, client, session) -> None:
        session.execute.return_value = _execute_returning([])

        response = client.post(
            "/api/v1/agents",
            json={"name": "Novo", "instructions": "x", "is_entry_point": True},
        )

        assert response.status_code == 201
        # UPDATE agents SET is_entry_point=false WHERE tenant_id=... roda antes do INSERT.
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        assert any("UPDATE agents" in s for s in statements)


class TestUpdate:
    def test_edita_nome_e_instrucoes(self, client, session) -> None:
        session.scalar.return_value = _agent()

        response = client.patch(
            f"/api/v1/agents/{AGENT_ID}", json={"name": "Secretária Nova"}
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Secretária Nova"

    def test_agente_de_outro_tenant_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.patch(f"/api/v1/agents/{AGENT_ID}", json={"name": "x"})

        assert response.status_code == 404

    def test_marcar_como_ponto_de_entrada_desmarca_o_anterior(self, client, session) -> None:
        session.scalar.return_value = _agent(is_entry_point=False)

        response = client.patch(
            f"/api/v1/agents/{AGENT_ID}", json={"is_entry_point": True}
        )

        assert response.status_code == 200
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        assert any("UPDATE agents" in s for s in statements)


class TestDelete:
    def test_apaga_agente_que_nao_e_ponto_de_entrada(self, client, session) -> None:
        session.scalar.side_effect = [_agent(is_entry_point=False), 2]

        response = client.delete(f"/api/v1/agents/{AGENT_ID}")

        assert response.status_code == 204
        session.delete.assert_awaited_once()

    def test_apagar_ponto_de_entrada_retorna_409(self, client, session) -> None:
        session.scalar.return_value = _agent(is_entry_point=True)

        response = client.delete(f"/api/v1/agents/{AGENT_ID}")

        assert response.status_code == 409
        session.delete.assert_not_awaited()

    def test_apagar_o_unico_agente_retorna_409(self, client, session) -> None:
        session.scalar.side_effect = [_agent(is_entry_point=False), 1]

        response = client.delete(f"/api/v1/agents/{AGENT_ID}")

        assert response.status_code == 409
        session.delete.assert_not_awaited()

    def test_agente_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.delete(f"/api/v1/agents/{AGENT_ID}")

        assert response.status_code == 404


class TestAttachKnowledgeBaseFile:
    def test_anexa_arquivo_existente(self, client, session) -> None:
        session.scalar.side_effect = [_agent(), SimpleNamespace(id=uuid.uuid4())]

        response = client.post(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files",
            json={"knowledge_base_file_id": str(uuid.uuid4())},
        )

        assert response.status_code == 201
        session.add.assert_called_once()
        session.commit.assert_awaited()

    def test_agente_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files",
            json={"knowledge_base_file_id": str(uuid.uuid4())},
        )

        assert response.status_code == 404

    def test_arquivo_de_outro_tenant_retorna_404(self, client, session) -> None:
        session.scalar.side_effect = [_agent(), None]

        response = client.post(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files",
            json={"knowledge_base_file_id": str(uuid.uuid4())},
        )

        assert response.status_code == 404


class TestDetachKnowledgeBaseFile:
    def test_desanexa_arquivo(self, client, session) -> None:
        session.scalar.return_value = _agent()
        link = SimpleNamespace(agent_id=AGENT_ID, knowledge_base_file_id=uuid.uuid4())
        session.get = AsyncMock(return_value=link)

        response = client.delete(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files/{link.knowledge_base_file_id}"
        )

        assert response.status_code == 204
        session.delete.assert_awaited_once_with(link)

    def test_vinculo_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = _agent()
        session.get = AsyncMock(return_value=None)

        response = client.delete(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files/{uuid.uuid4()}"
        )

        assert response.status_code == 404
