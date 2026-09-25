from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.documents.users import get_repo, router_doc_users


@pytest.fixture
def download_client(tmp_path, monkeypatch):
    tenant = str(uuid4())
    doc_id = str(uuid4())
    root = tmp_path / tenant / "kb"
    root.mkdir(parents=True)
    (root / "file.pdf").write_bytes(b"%PDF-test")
    monkeypatch.setenv("UPLOAD_DIR_USER", str(tmp_path))
    record = SimpleNamespace(
        tenant_id=tenant,
        conversation_id="kb",
        path_base=str(tmp_path),
        path_doc=f"{tenant}/kb",
        nome="file.pdf",
    )
    repo = AsyncMock()
    repo.buscar_documento_usuario_por_id.return_value = record
    app = FastAPI()
    app.include_router(router_doc_users)
    app.dependency_overrides[get_repo] = lambda: repo
    return TestClient(app), record, f"/documents/users/{doc_id}/content?tenant_id={tenant}"


def test_download_needs_authentication(download_client):
    client, _, url = download_client
    assert client.get(url).status_code == 403


def test_original_is_private(download_client):
    client, _, url = download_client
    response = client.get(url, headers={"Authorization": "test-api-key"})
    assert response.status_code == 200
    assert response.content == b"%PDF-test"
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("change", ["tenant", "conversation", "path", "missing"])
def test_download_rejects_other_scopes_and_paths(download_client, change):
    client, record, url = download_client
    if change == "tenant":
        record.tenant_id = str(uuid4())
    elif change == "conversation":
        record.conversation_id = "contact"
    elif change == "path":
        record.nome = "../../outside.pdf"
    else:
        record.nome = "missing.pdf"
    assert client.get(url, headers={"Authorization": "test-api-key"}).status_code == 404
