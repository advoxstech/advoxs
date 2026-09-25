import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from arq.worker import Retry

from app.config import settings
from app.tasks import knowledge_base as kb_task
from app.tasks.knowledge_base import ingest_knowledge_base_file

TENANT_ID = str(uuid.uuid4())
FILE_ID = str(uuid.uuid4())


def _ctx(job_try: int = 1) -> dict:
    session = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return {
        "session_factory": factory,
        "rag_http": AsyncMock(),
        "job_try": job_try,
        "_session": session,
    }


def _row(status: str = "processing") -> SimpleNamespace:
    return SimpleNamespace(filename="regimento.pdf", status=status)


@pytest.fixture
def temp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "kb_upload_dir", str(tmp_path))
    tenant_dir = tmp_path / TENANT_ID
    tenant_dir.mkdir(parents=True)
    path = tenant_dir / FILE_ID
    path.write_bytes(b"%PDF-1.4 conteudo")
    return path


@pytest.fixture
def patched(monkeypatch):
    mocks = {
        "load": AsyncMock(return_value=_row()),
        "ingest": AsyncMock(),
        "set_status": AsyncMock(),
    }
    monkeypatch.setattr(kb_task, "_load_file", mocks["load"])
    monkeypatch.setattr(kb_task, "ingest_document", mocks["ingest"])
    monkeypatch.setattr(kb_task, "_set_status", mocks["set_status"])
    return mocks


async def test_sucesso_marca_ready_e_apaga_temp(patched, temp_file) -> None:
    await ingest_knowledge_base_file(_ctx(), TENANT_ID, FILE_ID)

    kwargs = patched["ingest"].await_args.kwargs
    assert kwargs["tenant_id"] == TENANT_ID
    assert kwargs["doc_id"] == FILE_ID
    assert kwargs["filename"] == "regimento.pdf"
    assert kwargs["file_bytes"] == b"%PDF-1.4 conteudo"
    patched["set_status"].assert_awaited_once()
    assert patched["set_status"].await_args.args[2] == "ready"
    assert not temp_file.exists()


async def test_status_nao_processing_encerra(patched, temp_file) -> None:
    patched["load"].return_value = _row(status="ready")

    await ingest_knowledge_base_file(_ctx(), TENANT_ID, FILE_ID)

    patched["ingest"].assert_not_awaited()
    patched["set_status"].assert_not_awaited()


async def test_arquivo_sumido_marca_error(patched, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "kb_upload_dir", str(tmp_path))

    await ingest_knowledge_base_file(_ctx(), TENANT_ID, FILE_ID)

    patched["ingest"].assert_not_awaited()
    assert patched["set_status"].await_args.args[2] == "error"


async def test_erro_transiente_reagenda(patched, temp_file) -> None:
    patched["ingest"].side_effect = httpx.ConnectError("down")

    with pytest.raises(Retry):
        await ingest_knowledge_base_file(_ctx(job_try=1), TENANT_ID, FILE_ID)

    patched["set_status"].assert_not_awaited()


async def test_erro_transiente_na_ultima_tentativa_marca_error(patched, temp_file) -> None:
    patched["ingest"].side_effect = httpx.ConnectError("down")

    await ingest_knowledge_base_file(_ctx(job_try=5), TENANT_ID, FILE_ID)

    assert patched["set_status"].await_args.args[2] == "error"
    assert temp_file.exists()  # temp fica para eventual retry manual


async def test_erro_5xx_do_rag_reagenda(patched, temp_file) -> None:
    response = httpx.Response(500, request=httpx.Request("POST", "http://rag"))
    patched["ingest"].side_effect = httpx.HTTPStatusError(
        "500", request=response.request, response=response
    )

    with pytest.raises(Retry):
        await ingest_knowledge_base_file(_ctx(job_try=1), TENANT_ID, FILE_ID)

    patched["set_status"].assert_not_awaited()
    assert temp_file.exists()


async def test_erro_5xx_na_ultima_tentativa_preserva_arquivo(patched, temp_file) -> None:
    response = httpx.Response(500, request=httpx.Request("POST", "http://rag"))
    patched["ingest"].side_effect = httpx.HTTPStatusError(
        "500", request=response.request, response=response
    )

    await ingest_knowledge_base_file(_ctx(job_try=5), TENANT_ID, FILE_ID)

    args = patched["set_status"].await_args.args
    assert args[2] == "error"
    assert "500" in args[3]
    assert temp_file.exists()


async def test_erro_definitivo_4xx_marca_error(patched, temp_file) -> None:
    response = httpx.Response(400, request=httpx.Request("POST", "http://rag"), text="formato")
    patched["ingest"].side_effect = httpx.HTTPStatusError(
        "400", request=response.request, response=response
    )

    await ingest_knowledge_base_file(_ctx(job_try=1), TENANT_ID, FILE_ID)

    args = patched["set_status"].await_args.args
    assert args[2] == "error"
    assert "400" in args[3]


async def test_load_file_session_tem_tenant_id_setado(patched, temp_file) -> None:
    ctx = _ctx()

    await ingest_knowledge_base_file(ctx, TENANT_ID, FILE_ID)

    set_config_calls = [
        call
        for call in ctx["_session"].execute.await_args_list
        if len(call.args) > 1 and call.args[1] == {"tenant_id": TENANT_ID}
    ]
    assert len(set_config_calls) >= 1


async def test_set_status_recebe_tenant_id(patched, temp_file) -> None:
    await ingest_knowledge_base_file(_ctx(), TENANT_ID, FILE_ID)

    assert patched["set_status"].await_args.args[4] == TENANT_ID


async def test_replacement_published_only_after_success(patched, temp_file, monkeypatch):
    previous_id = uuid.uuid4()
    patched["load"].return_value.replaces_file_id = previous_id
    publish = AsyncMock()
    monkeypatch.setattr(kb_task, "_publish_replacement", publish)
    await ingest_knowledge_base_file(_ctx(), TENANT_ID, FILE_ID)
    patched["ingest"].assert_awaited_once()
    publish.assert_awaited_once()
    assert publish.await_args.args[1:] == (TENANT_ID, FILE_ID, previous_id)
    patched["set_status"].assert_not_awaited()


async def test_failed_ingestion_never_publishes_replacement(patched, temp_file, monkeypatch):
    patched["load"].return_value.replaces_file_id = uuid.uuid4()
    patched["ingest"].side_effect = httpx.ConnectError("down")
    publish = AsyncMock()
    monkeypatch.setattr(kb_task, "_publish_replacement", publish)
    await ingest_knowledge_base_file(_ctx(job_try=5), TENANT_ID, FILE_ID)
    publish.assert_not_awaited()
    assert temp_file.exists()
    assert patched["set_status"].await_args.args[2] == "error"


async def test_publication_failure_preserves_attempt_and_reports_retry(
    patched, temp_file, monkeypatch
):
    patched["load"].return_value.replaces_file_id = uuid.uuid4()
    monkeypatch.setattr(kb_task, "_publish_replacement", AsyncMock(side_effect=RuntimeError()))
    await ingest_knowledge_base_file(_ctx(job_try=5), TENANT_ID, FILE_ID)
    assert temp_file.exists()
    assert "anterior" in patched["set_status"].await_args.args[3]


async def test_publish_swaps_links_atomically_and_keeps_old_document():
    ctx = _ctx()
    previous = uuid.uuid4()
    candidate_result = MagicMock()
    candidate_result.one_or_none.return_value = SimpleNamespace(status="processing")
    previous_result = MagicMock()
    previous_result.one_or_none.return_value = SimpleNamespace(category="livros_digitais")
    ctx["_session"].execute.side_effect = [
        None,
        None,
        candidate_result,
        previous_result,
        None,
        None,
        None,
    ]
    await kb_task._publish_replacement(ctx["session_factory"], TENANT_ID, FILE_ID, previous)
    statements = [str(call.args[0]) for call in ctx["_session"].execute.await_args_list]
    assert "superseded_at" in statements[4]
    assert "replaces_file_id" in statements[5]
    assert "UPDATE agent_knowledge_base_files" in statements[6]
    assert all("DELETE" not in statement for statement in statements)
    ctx["_session"].commit.assert_awaited_once()


async def test_recovery_deduplicates_jobs_without_google_credentials():
    ctx = _ctx()
    ctx["system_session_factory"] = ctx["session_factory"]
    ctx["redis"] = AsyncMock()
    result = MagicMock()
    result.all.return_value = [SimpleNamespace(id=FILE_ID, tenant_id=TENANT_ID)]
    ctx["_session"].execute.return_value = result
    await kb_task.recover_drive_imports(ctx)
    ctx["redis"].enqueue_job.assert_awaited_once_with(
        "ingest_knowledge_base_file", tenant_id=TENANT_ID, file_id=FILE_ID, _job_id=f"kb:{FILE_ID}"
    )
