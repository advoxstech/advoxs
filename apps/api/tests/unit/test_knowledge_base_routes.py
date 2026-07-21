import io
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

import app.api.v1.knowledge_base as kb_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.rag import RagApiError
from app.core.config import settings
from app.core.queue import get_arq_pool
from app.main import app

TENANT_ID = uuid.uuid4()
FILE_ID = uuid.uuid4()


def _record(status: str = "ready") -> SimpleNamespace:
    return SimpleNamespace(
        id=FILE_ID,
        tenant_id=TENANT_ID,
        filename="regimento.pdf",
        size_bytes=1000,
        mime_type="application/pdf",
        status=status,
        error_message=None,
        uploaded_at=datetime.now(UTC),
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
        obj.uploaded_at = datetime.now(UTC)

    mock.refresh.side_effect = fake_refresh
    return mock


@pytest.fixture
def arq():
    return AsyncMock()


@pytest.fixture
def client(session, arq, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "kb_upload_dir", str(tmp_path))

    async def override_ctx():
        return TenantContext(user_id=uuid.uuid4(), tenant_id=TENANT_ID, role="admin")

    async def override_session():
        yield session

    async def override_arq():
        return arq

    app.dependency_overrides[get_current_tenant] = override_ctx
    app.dependency_overrides[get_tenant_session] = override_session
    app.dependency_overrides[get_arq_pool] = override_arq
    yield TestClient(app)
    app.dependency_overrides.clear()


AGENT_ID = uuid.uuid4()


def _upload(
    client,
    filename="regimento.pdf",
    content=b"%PDF-1.4 conteudo",
    mime="application/pdf",
    agent_id=None,
):
    return client.post(
        "/api/v1/knowledge-base/files",
        files={"file": (filename, io.BytesIO(content), mime)},
        data={"agent_id": str(agent_id or AGENT_ID)},
    )


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/knowledge-base/files")

    assert response.status_code == 401


class TestUpload:
    def test_upload_feliz_enfileira_apos_commit(self, client, session, arq, tmp_path) -> None:
        session.execute.return_value = _active_subscription()
        # 1ª scalar: agente-destino válido; 2ª: soma do storage usado;
        # 3ª: contagem de arquivos; 4ª: checagem de duplicado.
        session.scalar.side_effect = [
            SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID),
            0,
            0,
            None,
        ]

        response = _upload(client)

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "processing"
        assert body["filename"] == "regimento.pdf"
        session.commit.assert_awaited()
        arq.enqueue_job.assert_awaited_once()
        kwargs = arq.enqueue_job.await_args.kwargs
        assert kwargs["tenant_id"] == str(TENANT_ID)
        saved = tmp_path / str(TENANT_ID) / kwargs["file_id"]
        assert saved.read_bytes() == b"%PDF-1.4 conteudo"

    def test_extensao_invalida_400(self, client, session) -> None:
        session.execute.return_value = _active_subscription()

        response = _upload(client, filename="malware.exe", mime="application/octet-stream")

        assert response.status_code == 400

    def test_mime_incompativel_400(self, client, session) -> None:
        session.execute.return_value = _active_subscription()

        response = _upload(client, filename="regimento.pdf", mime="text/plain")

        assert response.status_code == 400

    def test_arquivo_vazio_400(self, client, session) -> None:
        session.execute.return_value = _active_subscription()
        # 1ª scalar: agente-destino válido; 2ª: soma do storage usado; 3ª: contagem de arquivos.
        session.scalar.side_effect = [SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID), 0, 0]

        response = _upload(client, content=b"")

        assert response.status_code == 400

    def test_arquivo_grande_413(self, client, session, monkeypatch) -> None:
        session.execute.return_value = _active_subscription()
        monkeypatch.setattr(settings, "kb_max_file_size_bytes", 10)

        response = _upload(client, content=b"x" * 11)

        assert response.status_code == 413

    def test_storage_estourado_413(self, client, session) -> None:
        session.execute.return_value = _active_subscription(
            {"max_knowledge_base_storage_bytes": 100}
        )
        # 1ª scalar: agente-destino válido; 2ª: soma do storage usado.
        session.scalar.side_effect = [SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID), 95]

        response = _upload(client, content=b"x" * 10)

        assert response.status_code == 413
        assert "restam" in response.json()["detail"]

    def test_nome_duplicado_409(self, client, session) -> None:
        session.execute.return_value = _active_subscription()
        # 1ª scalar: agente-destino válido; 2ª: soma do storage usado;
        # 3ª: contagem de arquivos; 4ª: checagem de duplicado.
        session.scalar.side_effect = [
            SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID),
            0,
            0,
            FILE_ID,
        ]

        response = _upload(client)

        assert response.status_code == 409

    def test_corrida_de_duplicado_no_commit_409(self, client, session, tmp_path) -> None:
        session.execute.return_value = _active_subscription()
        # Dois uploads concorrentes passam pelo check de duplicado; a unique
        # constraint (tenant_id, filename) estoura no commit do segundo.
        # 1ª scalar: agente-destino válido; 2ª: soma do storage usado;
        # 3ª: contagem de arquivos; 4ª: checagem de duplicado.
        session.scalar.side_effect = [SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID), 0, 0, None]
        session.commit.side_effect = IntegrityError("stmt", {}, Exception("uq"))

        response = _upload(client)

        assert response.status_code == 409
        tenant_dir = tmp_path / str(TENANT_ID)
        assert not tenant_dir.exists() or not any(tenant_dir.iterdir())

    def test_upload_com_agente_de_outro_tenant_retorna_404(self, client, session) -> None:
        # agent_id explícito não encontrado (tenant errado ou inexistente) —
        # 404, comportamento inalterado em relação a antes do fallback.
        session.scalar.side_effect = [None]

        response = _upload(client)

        assert response.status_code == 404

    def test_upload_sem_agent_id_usa_agente_ponto_de_entrada_do_tenant(
        self, client, session
    ) -> None:
        # Sem agent_id no form (cliente web atual, que não manda esse campo)
        # cai no fallback: o ponto de entrada do tenant vira o agente-destino.
        session.execute.return_value = _active_subscription()
        entry_point_id = uuid.uuid4()
        session.scalar.side_effect = [
            SimpleNamespace(id=entry_point_id, tenant_id=TENANT_ID, is_entry_point=True),
            0,
            0,
            None,
        ]

        response = client.post(
            "/api/v1/knowledge-base/files",
            files={"file": ("regimento.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )

        assert response.status_code == 202
        link_calls = [
            call.args[0]
            for call in session.add.call_args_list
            if type(call.args[0]).__name__ == "AgentKnowledgeBaseFile"
        ]
        assert len(link_calls) == 1
        assert link_calls[0].agent_id == entry_point_id

    def test_upload_sem_agent_id_sem_ponto_de_entrada_retorna_500(self, client, session) -> None:
        # Defensivo: se o tenant não tem NENHUM agente ponto de entrada
        # (não devia acontecer, mas não pode quebrar em silêncio) — 500
        # explícito em vez de um erro de banco/None mais adiante.
        session.scalar.side_effect = [None]

        response = client.post(
            "/api/v1/knowledge-base/files",
            files={"file": ("regimento.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )

        assert response.status_code == 500

    def test_limite_de_arquivos_do_plano_retorna_409(self, client, session) -> None:
        session.execute.return_value = _active_subscription({"max_knowledge_base_files": 2})
        # 1ª scalar: agente-destino válido; 2ª: soma do storage usado;
        # 3ª: contagem de arquivos (no teto).
        session.scalar.side_effect = [SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID), 0, 2]

        response = _upload(client)

        assert response.status_code == 409
        assert "arquivos" in response.json()["detail"].lower()

    def test_assinatura_inativa_retorna_409(self, client, session) -> None:
        session.execute.return_value = _active_subscription(
            subscription_overrides={"status": "past_due"}
        )
        session.scalar.side_effect = [SimpleNamespace(id=AGENT_ID, tenant_id=TENANT_ID)]

        response = _upload(client)

        assert response.status_code == 409
        assert "assinatura" in response.json()["detail"].lower()


class TestList:
    def test_lista_arquivos_do_tenant(self, client, session) -> None:
        result = MagicMock()
        result.scalars.return_value.all.return_value = [_record()]
        session.execute.return_value = result

        response = client.get("/api/v1/knowledge-base/files")

        assert response.status_code == 200
        assert response.json()[0]["filename"] == "regimento.pdf"


class TestDelete:
    @pytest.fixture
    def rag_delete(self, monkeypatch):
        mock = AsyncMock()
        monkeypatch.setattr(kb_module, "delete_documents", mock)
        return mock

    def test_delete_feliz_204(self, client, session, rag_delete) -> None:
        session.scalar.return_value = _record(status="ready")

        response = client.delete(f"/api/v1/knowledge-base/files/{FILE_ID}")

        assert response.status_code == 204
        rag_delete.assert_awaited_once_with(str(TENANT_ID), [str(FILE_ID)])
        session.delete.assert_awaited_once()

    def test_delete_durante_processing_409(self, client, session, rag_delete) -> None:
        session.scalar.return_value = _record(status="processing")

        response = client.delete(f"/api/v1/knowledge-base/files/{FILE_ID}")

        assert response.status_code == 409
        rag_delete.assert_not_awaited()

    def test_delete_inexistente_404(self, client, session, rag_delete) -> None:
        session.scalar.return_value = None

        response = client.delete(f"/api/v1/knowledge-base/files/{uuid.uuid4()}")

        assert response.status_code == 404

    def test_rag_indisponivel_502(self, client, session, rag_delete) -> None:
        session.scalar.return_value = _record(status="ready")
        rag_delete.side_effect = RagApiError("api_rag indisponível")

        response = client.delete(f"/api/v1/knowledge-base/files/{FILE_ID}")

        assert response.status_code == 502
        session.delete.assert_not_awaited()
