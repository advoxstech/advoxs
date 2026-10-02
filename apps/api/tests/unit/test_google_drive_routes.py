import hashlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.api.v1 import google_drive as routes
from app.clients.google_drive import DriveFile
from app.core.config import settings
from app.core.queue import get_arq_pool
from app.main import app
from app.models import AgentKnowledgeBaseFile, KnowledgeBaseFile

TENANT = uuid.uuid4()
AGENT = uuid.uuid4()
OLD = uuid.uuid4()
TOKEN = "private-google-token"


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "google_drive_enabled", True)
    monkeypatch.setattr(settings, "google_drive_client_id", "client")
    monkeypatch.setattr(settings, "google_drive_api_key", "key")
    monkeypatch.setattr(settings, "google_drive_project_number", "123")
    monkeypatch.setattr(settings, "kb_upload_dir", str(tmp_path))
    session = AsyncMock()
    session.add = MagicMock()
    session.scalar.side_effect = [SimpleNamespace(id=AGENT), None, None, 0]
    plan = SimpleNamespace(max_knowledge_base_files=None, max_knowledge_base_storage_bytes=None)
    monkeypatch.setattr(
        routes,
        "get_active_subscription",
        AsyncMock(return_value=(SimpleNamespace(status="active"), plan)),
    )
    metadata = AsyncMock(
        return_value=DriveFile("drive-id", "normas.pdf", "application/pdf", "2", False)
    )
    download = AsyncMock(return_value=b"%PDF-1.4 new-content")
    monkeypatch.setattr(routes.GoogleDrive, "metadata", metadata)
    monkeypatch.setattr(routes.GoogleDrive, "download", download)
    queue = AsyncMock()

    async def ctx():
        return TenantContext(user_id=uuid.uuid4(), tenant_id=TENANT, role="admin")

    async def db():
        yield session

    async def arq():
        return queue

    app.dependency_overrides.update(
        {get_current_tenant: ctx, get_tenant_session: db, get_arq_pool: arq}
    )
    yield SimpleNamespace(
        client=TestClient(app),
        session=session,
        queue=queue,
        plan=plan,
        metadata=metadata,
        download=download,
        path=tmp_path,
    )
    app.dependency_overrides.clear()


def payload(**kwargs):
    return {
        "access_token": TOKEN,
        "file_id": "drive-id",
        "agent_id": str(AGENT),
        "expected_version": "2",
        **kwargs,
    }


def old_file(**kwargs):
    return KnowledgeBaseFile(
        id=OLD,
        tenant_id=TENANT,
        filename="normas.pdf",
        size_bytes=10,
        mime_type="application/pdf",
        status="ready",
        category="livros_digitais",
        drive_file_id="drive-id",
        drive_version="1",
        uploaded_at=datetime.now(UTC),
        **kwargs,
    )


def test_disabled_feature_has_no_configuration_and_no_download(setup, monkeypatch):
    monkeypatch.setattr(settings, "google_drive_enabled", False)
    assert setup.client.get("/api/v1/knowledge-base/drive/config").json() == {"enabled": False}
    assert (
        setup.client.post("/api/v1/knowledge-base/drive/import", json=payload()).status_code == 503
    )
    setup.download.assert_not_awaited()


def test_incomplete_config_disables_feature(setup, monkeypatch):
    monkeypatch.setattr(settings, "google_drive_client_id", " ")
    assert setup.client.get("/api/v1/knowledge-base/drive/config").json() == {"enabled": False}


def test_configuration_and_import_require_advoxs_session():
    client = TestClient(app)
    assert client.get("/api/v1/knowledge-base/drive/config").status_code == 401


def test_new_import_saves_only_document_metadata_and_enqueues_after_commit(setup):
    response = setup.client.post("/api/v1/knowledge-base/drive/import", json=payload())
    assert response.status_code == 202
    assert response.json()["result"] == "processing"
    record = next(
        call.args[0]
        for call in setup.session.add.call_args_list
        if isinstance(call.args[0], KnowledgeBaseFile)
    )
    assert record.tenant_id == TENANT
    assert record.drive_file_id == "drive-id"
    assert record.content_sha256 == hashlib.sha256(b"%PDF-1.4 new-content").hexdigest()
    assert (setup.path / str(TENANT) / str(record.id)).read_bytes().startswith(b"%PDF")
    assert TOKEN not in str(record.__dict__)
    assert TOKEN not in str(setup.queue.enqueue_job.call_args)
    setup.session.commit.assert_awaited_once()
    setup.queue.enqueue_job.assert_awaited_once()
    assert any(
        isinstance(call.args[0], AgentKnowledgeBaseFile)
        for call in setup.session.add.call_args_list
    )


def test_replacement_staged_without_exposing_new_content_to_agents(setup):
    previous = old_file()
    setup.session.scalar.side_effect = [SimpleNamespace(id=AGENT), previous, None, 10]
    response = setup.client.post(
        "/api/v1/knowledge-base/drive/import", json=payload(replace_file_id=str(OLD))
    )
    assert response.status_code == 202
    record = setup.session.add.call_args.args[0]
    assert record.replaces_file_id == OLD
    assert record.category == previous.category
    assert previous.status == "ready" and previous.superseded_at is None
    assert not any(
        isinstance(call.args[0], AgentKnowledgeBaseFile)
        for call in setup.session.add.call_args_list
    )


def test_replacement_requires_explicit_confirmation(setup):
    setup.session.scalar.side_effect = [SimpleNamespace(id=AGENT), old_file()]
    response = setup.client.post("/api/v1/knowledge-base/drive/import", json=payload())
    assert response.status_code == 409
    setup.session.add.assert_not_called()


def test_identical_bytes_skipped_even_if_drive_version_changed(setup):
    setup.session.scalar.side_effect = [
        SimpleNamespace(id=AGENT),
        old_file(content_sha256=hashlib.sha256(b"%PDF-1.4 new-content").hexdigest()),
    ]
    response = setup.client.post(
        "/api/v1/knowledge-base/drive/import", json=payload(replace_file_id=str(OLD))
    )
    assert response.json()["result"] == "unchanged"
    setup.session.add.assert_not_called()


def test_cross_tenant_agent_not_found_before_download(setup):
    setup.session.scalar.side_effect = [None]
    assert (
        setup.client.post("/api/v1/knowledge-base/drive/import", json=payload()).status_code == 404
    )
    setup.download.assert_not_awaited()
    query = setup.session.scalar.await_args.args[0].compile(dialect=postgresql.dialect())
    assert TENANT in query.params.values()


def test_wrong_replacement_cannot_overwrite_other_file(setup):
    setup.session.scalar.side_effect = [SimpleNamespace(id=AGENT), old_file()]
    response = setup.client.post(
        "/api/v1/knowledge-base/drive/import", json=payload(replace_file_id=str(uuid.uuid4()))
    )
    assert response.status_code == 409
    setup.session.add.assert_not_called()


def test_drive_format_change_cannot_publish_wrong_extension(setup):
    previous = old_file()
    previous.mime_type = "text/plain"
    setup.session.scalar.side_effect = [SimpleNamespace(id=AGENT), previous]
    response = setup.client.post(
        "/api/v1/knowledge-base/drive/import", json=payload(replace_file_id=str(OLD))
    )
    assert response.status_code == 409
    assert "formato mudou" in response.json()["detail"]
    setup.session.add.assert_not_called()


@pytest.mark.parametrize("case,expected", [("storage", 413), ("count", 409), ("name", 409)])
def test_limits_and_name_conflict(setup, case, expected):
    if case == "storage":
        setup.plan.max_knowledge_base_storage_bytes = 1
    elif case == "count":
        setup.plan.max_knowledge_base_files = 1
        setup.session.scalar.side_effect = [SimpleNamespace(id=AGENT), None, None, 0, 1]
    else:
        setup.session.scalar.side_effect = [SimpleNamespace(id=AGENT), None, uuid.uuid4()]
    assert (
        setup.client.post("/api/v1/knowledge-base/drive/import", json=payload()).status_code
        == expected
    )
    setup.session.add.assert_not_called()


def test_duplicate_pending_update_rejected(setup):
    setup.session.scalar.side_effect = [SimpleNamespace(id=AGENT), old_file(), uuid.uuid4()]
    response = setup.client.post(
        "/api/v1/knowledge-base/drive/import", json=payload(replace_file_id=str(OLD))
    )
    assert response.status_code == 409


def test_changed_version_requires_new_preview(setup):
    response = setup.client.post(
        "/api/v1/knowledge-base/drive/import", json=payload(expected_version="1")
    )
    assert response.status_code == 409
    setup.download.assert_not_awaited()


def test_fake_pdf_rejected(setup):
    setup.download.return_value = b"not a PDF"
    assert (
        setup.client.post("/api/v1/knowledge-base/drive/import", json=payload()).status_code == 400
    )
    setup.session.add.assert_not_called()


def test_queue_failure_can_be_retried_without_google_token(setup):
    setup.queue.enqueue_job.side_effect = RuntimeError("queue down")
    response = setup.client.post("/api/v1/knowledge-base/drive/import", json=payload())
    assert response.json()["result"] == "error"
    record = setup.session.add.call_args_list[0].args[0]
    assert record.status == "error"
    assert (setup.path / str(TENANT) / str(record.id)).exists()


def test_database_failure_cleans_temporary_file(setup):
    from sqlalchemy.exc import IntegrityError

    setup.session.commit.side_effect = IntegrityError("insert", {}, Exception())
    assert (
        setup.client.post("/api/v1/knowledge-base/drive/import", json=payload()).status_code == 409
    )
    assert not list(setup.path.rglob("*.pdf"))
    assert not [path for path in setup.path.rglob("*") if path.is_file()]
    setup.queue.enqueue_job.assert_not_awaited()


@pytest.mark.parametrize(
    "state,version,action",
    [("ready", "1", "update"), ("ready", "2", "unchanged"), ("error", "1", "pending")],
)
def test_preview_existing_file(setup, state, version, action):
    previous = old_file()
    previous.status, previous.drive_version = state, version
    setup.session.scalar.side_effect = [previous, None]
    response = setup.client.post("/api/v1/knowledge-base/drive/preview", json=payload())
    assert response.json()["action"] == action


def test_url_instead_of_file_id_rejected(setup):
    response = setup.client.post(
        "/api/v1/knowledge-base/drive/import", json=payload(file_id="https://localhost/secrets")
    )
    assert response.status_code == 422
    setup.metadata.assert_not_awaited()
    assert TOKEN not in response.text


@pytest.mark.parametrize("pending", [False, True])
async def test_delete_drive_current_removes_history_but_attempt_preserves_current(
    setup, monkeypatch, pending
):
    from app.api.v1 import knowledge_base as kb

    record = old_file()
    record.replaces_file_id = uuid.uuid4() if pending else None
    older = old_file()
    older.id = uuid.uuid4()
    older.superseded_at = datetime.now(UTC)
    result = MagicMock()
    result.scalars.return_value.all.return_value = [record, older]
    setup.session.execute.return_value = result
    delete = AsyncMock()
    monkeypatch.setattr(kb, "delete_documents", delete)
    ctx = TenantContext(user_id=uuid.uuid4(), tenant_id=TENANT, role="admin")
    await kb._delete_drive_files(record, ctx, setup.session)
    expected = [str(record.id)] if pending else [str(record.id), str(older.id)]
    delete.assert_awaited_once_with(str(TENANT), expected)
    assert setup.session.delete.await_count == len(expected)
    if not pending:
        query = setup.session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
        assert TENANT in query.params.values()


async def test_delete_processing_replacement_preserves_current(setup, monkeypatch):
    from fastapi import HTTPException

    from app.api.v1 import knowledge_base as kb

    record = old_file()
    record.status = "processing"
    record.replaces_file_id = uuid.uuid4()
    delete = AsyncMock()
    monkeypatch.setattr(kb, "delete_documents", delete)
    ctx = TenantContext(user_id=uuid.uuid4(), tenant_id=TENANT, role="admin")
    with pytest.raises(HTTPException) as caught:
        await kb._delete_drive_files(record, ctx, setup.session)
    assert caught.value.status_code == 409
    delete.assert_not_awaited()


def test_list_pending_revision_uses_previous_agents_only_for_display(setup):
    record = old_file()
    staged = old_file()
    staged.id = uuid.uuid4()
    staged.replaces_file_id = OLD
    staged.status = "processing"
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [record, staged]
    links = MagicMock()
    links.all.return_value = [(OLD, AGENT)]
    setup.session.execute.side_effect = [rows, links]
    response = setup.client.get("/api/v1/knowledge-base/files")
    assert response.status_code == 200
    assert response.json()[1]["agent_ids"] == [str(AGENT)]
    assert response.json()[1]["replaces_file_id"] == str(OLD)
    setup.session.add.assert_not_called()
