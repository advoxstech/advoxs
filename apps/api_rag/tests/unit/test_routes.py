from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.routes.documents.users import get_service
from api.routes.retrievals import get_retrieval
from constants import SYSTEM_TENANT_ID
from main import app

HEADERS = {"Authorization": "test-api-key"}


@pytest.fixture
def retrieval_service():
    svc = AsyncMock()
    svc.search_hybrid.return_value = []
    return svc


@pytest.fixture
def documento_service():
    return AsyncMock()


@pytest.fixture
def client(retrieval_service, documento_service):
    app.dependency_overrides[get_retrieval] = lambda: retrieval_service
    app.dependency_overrides[get_service] = lambda: documento_service
    # Sem context manager: não dispara o lifespan (Postgres/Qdrant reais).
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestAuth:
    def test_sem_api_key_retorna_403(self, client) -> None:
        response = client.post(
            "/retrieval/users",
            json={"tenant_id": "t1", "conversation_id": "c1", "message": "oi"},
        )

        assert response.status_code == 403

    def test_api_key_errada_retorna_403(self, client) -> None:
        response = client.post(
            "/retrieval/users",
            json={"tenant_id": "t1", "conversation_id": "c1", "message": "oi"},
            headers={"Authorization": "errada"},
        )

        assert response.status_code == 403


class TestRetrievalUsers:
    def test_exige_tenant_id_no_body(self, client) -> None:
        response = client.post(
            "/retrieval/users",
            json={"conversation_id": "c1", "message": "oi"},
            headers=HEADERS,
        )

        assert response.status_code == 422

    def test_busca_escopada_por_tenant_e_conversa(self, client, retrieval_service) -> None:
        response = client.post(
            "/retrieval/users",
            json={"tenant_id": "t1", "conversation_id": "c1", "message": "meu contrato"},
            headers=HEADERS,
        )

        assert response.status_code == 200
        retrieval_service.search_hybrid.assert_awaited_once_with(
            query="meu contrato",
            tenant_id="t1",
            extra_filters={"conversation_id": "c1"},
        )


class TestRetrievalSystem:
    def test_usa_tenant_reservado_do_sistema(self, client, retrieval_service) -> None:
        response = client.post(
            "/retrieval/system",
            json={"base": "condominial", "message": "convenção"},
            headers=HEADERS,
        )

        assert response.status_code == 200
        retrieval_service.search_hybrid.assert_awaited_once_with(
            query="convenção",
            tenant_id=SYSTEM_TENANT_ID,
            extra_filters={"base": "condominial"},
        )


class TestDocumentsUsers:
    def test_insert_exige_tenant_id(self, client) -> None:
        response = client.post(
            "/documents/users/insert",
            data={"conversation_id": "c1"},
            files={"file": ("a.pdf", b"%PDF", "application/pdf")},
            headers=HEADERS,
        )

        assert response.status_code == 422

    def test_insert_passa_tenant_e_conversa(self, client, documento_service) -> None:
        response = client.post(
            "/documents/users/insert",
            data={"tenant_id": "t1", "conversation_id": "c1"},
            files={"file": ("a.pdf", b"%PDF", "application/pdf")},
            headers=HEADERS,
        )

        assert response.status_code == 200
        args = documento_service.inserir_documento_usuario.await_args.args
        assert args[1] == "t1"
        assert args[2] == "c1"

    def test_delete_exige_tenant_id(self, client) -> None:
        response = client.delete(
            "/documents/users/delete",
            params={"docs_ids": ["abc"]},
            headers=HEADERS,
        )

        assert response.status_code == 422

    def test_delete_passa_tenant(self, client, documento_service) -> None:
        response = client.delete(
            "/documents/users/delete",
            params={"tenant_id": "t1", "docs_ids": ["d1", "d2"]},
            headers=HEADERS,
        )

        assert response.status_code == 200
        documento_service.deletar_documento_usuario.assert_awaited_once_with("t1", ["d1", "d2"])
