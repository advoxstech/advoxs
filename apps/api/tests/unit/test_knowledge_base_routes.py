import io
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

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


def _upload(client, filename="regimento.pdf", content=b"%PDF-1.4 conteudo", mime="application/pdf"):
    return client.post(
        "/api/v1/knowledge-base/files",
        files={"file": (filename, io.BytesIO(content), mime)},
    )


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/knowledge-base/files")

    assert response.status_code == 401


class TestUpload:
    def test_upload_feliz_enfileira_apos_commit(self, client, session, arq, tmp_path) -> None:
        # 1ª scalar: soma do storage usado; 2ª: checagem de duplicado.
        session.scalar.side_effect = [0, None]

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

    def test_extensao_invalida_400(self, client) -> None:
        response = _upload(client, filename="malware.exe", mime="application/octet-stream")

        assert response.status_code == 400

    def test_mime_incompativel_400(self, client) -> None:
        response = _upload(client, filename="regimento.pdf", mime="text/plain")

        assert response.status_code == 400

    def test_arquivo_vazio_400(self, client, session) -> None:
        session.scalar.side_effect = [0, None]

        response = _upload(client, content=b"")

        assert response.status_code == 400

    def test_arquivo_grande_413(self, client, monkeypatch) -> None:
        monkeypatch.setattr(settings, "kb_max_file_size_bytes", 10)

        response = _upload(client, content=b"x" * 11)

        assert response.status_code == 413

    def test_storage_estourado_413(self, client, session, monkeypatch) -> None:
        monkeypatch.setattr(settings, "kb_max_total_size_bytes", 100)
        session.scalar.side_effect = [95]

        response = _upload(client, content=b"x" * 10)

        assert response.status_code == 413
        assert "restam" in response.json()["detail"]

    def test_nome_duplicado_409(self, client, session) -> None:
        session.scalar.side_effect = [0, FILE_ID]

        response = _upload(client)

        assert response.status_code == 409


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
