import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

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


def _active_subscription(
    plan_overrides: dict | None = None, subscription_overrides: dict | None = None
) -> MagicMock:
    plan_defaults = {
        "id": uuid.uuid4(),
        "name": "Profissional",
        "max_agents": None,
        "max_extra_tools": None,
        "max_knowledge_base_files": None,
        "max_knowledge_base_storage_bytes": None,
        "monthly_credits_granted": 1000,
        "is_legacy": False,
        "active": True,
    }
    plan = SimpleNamespace(**{**plan_defaults, **(plan_overrides or {})})
    subscription_defaults = {"status": "active"}
    subscription = SimpleNamespace(**{**subscription_defaults, **(subscription_overrides or {})})
    result = MagicMock()
    result.one_or_none.return_value = (subscription, plan)
    return result


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
        session.execute.return_value = _active_subscription()

        response = client.post(
            "/api/v1/agents",
            json={"name": "Vendas", "instructions": "Você vende planos."},
        )

        assert response.status_code == 201
        assert response.json()["name"] == "Vendas"
        assert response.json()["is_entry_point"] is False
        session.add.assert_called_once()
        added = session.add.call_args.args[0]
        assert added.tenant_id == TENANT_ID
        session.commit.assert_awaited()

    def test_criar_agente_com_is_entry_point_no_payload_e_ignorado(self, client, session) -> None:
        """is_entry_point não existe mais em AgentCreate — mesmo se vier no
        payload, é ignorado (Pydantic descarta campo não declarado). Todo
        agente criado via API nasce sempre com is_entry_point=False, e
        nenhuma UPDATE de desmarcar o ponto de entrada anterior roda."""
        session.execute.return_value = _active_subscription()

        response = client.post(
            "/api/v1/agents",
            json={"name": "Novo", "instructions": "x", "is_entry_point": True},
        )

        assert response.status_code == 201
        assert response.json()["is_entry_point"] is False
        added = session.add.call_args.args[0]
        assert added.is_entry_point is False
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        assert not any("UPDATE agents" in s for s in statements)

    def test_limite_de_agentes_do_plano_retorna_409(self, client, session) -> None:
        session.execute.return_value = _active_subscription({"max_agents": 2})
        session.scalar.return_value = 2

        response = client.post(
            "/api/v1/agents",
            json={"name": "Novo", "instructions": "x", "is_entry_point": False},
        )

        assert response.status_code == 409
        assert "agentes" in response.json()["detail"].lower()

    def test_assinatura_inativa_retorna_409(self, client, session) -> None:
        session.execute.return_value = _active_subscription(
            subscription_overrides={"status": "past_due"}
        )

        response = client.post(
            "/api/v1/agents",
            json={"name": "Novo", "instructions": "x", "is_entry_point": False},
        )

        assert response.status_code == 409
        assert "assinatura" in response.json()["detail"].lower()


class TestUpdate:
    def test_edita_nome_e_instrucoes(self, client, session) -> None:
        session.scalar.return_value = _agent()

        response = client.patch(f"/api/v1/agents/{AGENT_ID}", json={"name": "Secretária Nova"})

        assert response.status_code == 200
        assert response.json()["name"] == "Secretária Nova"

    def test_agente_de_outro_tenant_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.patch(f"/api/v1/agents/{AGENT_ID}", json={"name": "x"})

        assert response.status_code == 404

    def test_patch_ignora_is_entry_point_true_no_payload(self, client, session) -> None:
        """is_entry_point não existe mais em AgentUpdate — enviar True não
        promove o agente a ponto de entrada."""
        session.scalar.return_value = _agent(is_entry_point=False)

        response = client.patch(f"/api/v1/agents/{AGENT_ID}", json={"is_entry_point": True})

        assert response.status_code == 200
        assert response.json()["is_entry_point"] is False
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        assert not any("UPDATE agents" in s for s in statements)

    def test_patch_ignora_is_entry_point_false_no_payload(self, client, session) -> None:
        """Enviar False também não desmarca — o ponto de entrada atual do
        tenant nunca muda via PATCH, em nenhuma direção."""
        session.scalar.return_value = _agent(is_entry_point=True)

        response = client.patch(
            f"/api/v1/agents/{AGENT_ID}", json={"name": "Nome novo", "is_entry_point": False}
        )

        assert response.status_code == 200
        assert response.json()["is_entry_point"] is True
        assert response.json()["name"] == "Nome novo"
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        assert not any("UPDATE agents" in s for s in statements)


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

    def test_arquivo_ja_anexado_retorna_409(self, client, session) -> None:
        session.scalar.side_effect = [_agent(), SimpleNamespace(id=uuid.uuid4())]
        session.commit.side_effect = IntegrityError("stmt", {}, Exception("dup"))

        response = client.post(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files",
            json={"knowledge_base_file_id": str(uuid.uuid4())},
        )

        assert response.status_code == 409
        session.rollback.assert_awaited_once()


class TestDetachKnowledgeBaseFile:
    def test_desanexa_arquivo(self, client, session) -> None:
        # 1ª scalar: _get_agent; 2ª: contagem de vínculos do arquivo (>1, permite).
        session.scalar.side_effect = [_agent(), 2]
        link = SimpleNamespace(agent_id=AGENT_ID, knowledge_base_file_id=uuid.uuid4())
        session.get = AsyncMock(return_value=link)

        response = client.delete(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files/{link.knowledge_base_file_id}"
        )

        assert response.status_code == 204
        session.delete.assert_awaited_once_with(link)

    def test_desanexar_ultimo_vinculo_retorna_409(self, client, session) -> None:
        session.scalar.side_effect = [_agent(), 1]
        link = SimpleNamespace(agent_id=AGENT_ID, knowledge_base_file_id=uuid.uuid4())
        session.get = AsyncMock(return_value=link)

        response = client.delete(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files/{link.knowledge_base_file_id}"
        )

        assert response.status_code == 409
        session.delete.assert_not_awaited()

    def test_vinculo_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = _agent()
        session.get = AsyncMock(return_value=None)

        response = client.delete(f"/api/v1/agents/{AGENT_ID}/knowledge-base-files/{uuid.uuid4()}")

        assert response.status_code == 404


class TestListKnowledgeBaseFiles:
    def test_lista_arquivos_anexados(self, client, session) -> None:
        session.scalar.return_value = _agent()
        file_row = SimpleNamespace(
            id=uuid.uuid4(),
            filename="regimento.pdf",
            size_bytes=1024,
            mime_type="application/pdf",
            status="ready",
            error_message=None,
            uploaded_at=datetime.now(UTC),
        )
        session.execute.return_value = _execute_returning([file_row])

        response = client.get(f"/api/v1/agents/{AGENT_ID}/knowledge-base-files")

        assert response.status_code == 200
        assert response.json()[0]["filename"] == "regimento.pdf"

    def test_agente_inexistente_retorna_404(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.get(f"/api/v1/agents/{AGENT_ID}/knowledge-base-files")

        assert response.status_code == 404
