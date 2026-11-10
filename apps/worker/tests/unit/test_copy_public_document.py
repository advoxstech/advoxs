import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from arq.worker import Retry

from app.tasks import knowledge_base as kb_task
from app.tasks.knowledge_base import PUBLIC_TENANT_ID, copy_public_document

TENANT_ID = str(uuid.uuid4())
FILE_ID = str(uuid.uuid4())
PUBLIC_DOCUMENT_ID = uuid.uuid4()


def _ctx(row, job_try: int = 1) -> dict:
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(one_or_none=lambda: row)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return {"session_factory": factory, "rag_http": AsyncMock(), "job_try": job_try}


def _row(status: str = "processing", public_document_id=PUBLIC_DOCUMENT_ID) -> SimpleNamespace:
    return SimpleNamespace(
        filename="1.01_Codigo_Civil.pdf", status=status, public_document_id=public_document_id
    )


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://api_rag/documents/users/copy")
    return httpx.HTTPStatusError(
        "erro", request=request, response=httpx.Response(status_code, request=request)
    )


@pytest.fixture
def patched(monkeypatch):
    mocks = {"copy": AsyncMock(), "set_status": AsyncMock()}
    monkeypatch.setattr(kb_task, "copy_document", mocks["copy"])
    monkeypatch.setattr(kb_task, "_set_status", mocks["set_status"])
    return mocks


async def test_copia_da_base_central_e_marca_pronto(patched) -> None:
    ctx = _ctx(_row())

    await copy_public_document(ctx, TENANT_ID, FILE_ID)

    assert patched["copy"].await_args.kwargs == {
        "source_tenant_id": PUBLIC_TENANT_ID,
        "source_doc_id": str(PUBLIC_DOCUMENT_ID),
        "tenant_id": TENANT_ID,
        "doc_id": FILE_ID,
        "filename": "1.01_Codigo_Civil.pdf",
    }
    patched["set_status"].assert_awaited_once_with(
        ctx["session_factory"], FILE_ID, "ready", None, TENANT_ID
    )


@pytest.mark.parametrize(
    "row", [None, _row(status="ready"), _row(public_document_id=None)], ids=["sumiu", "pronto", "x"]
)
async def test_nao_copia_o_que_nao_e_copia_de_pacote_pendente(patched, row) -> None:
    await copy_public_document(_ctx(row), TENANT_ID, FILE_ID)

    patched["copy"].assert_not_awaited()
    patched["set_status"].assert_not_awaited()


async def test_recusa_do_api_rag_vira_erro_com_orientacao(patched) -> None:
    patched["copy"].side_effect = _http_error(400)

    await copy_public_document(_ctx(_row()), TENANT_ID, FILE_ID)

    args = patched["set_status"].await_args.args
    assert args[2] == "error"
    assert "Reprocessar" in args[3]


async def test_falha_temporaria_reagenda(patched) -> None:
    patched["copy"].side_effect = _http_error(503)

    with pytest.raises(Retry):
        await copy_public_document(_ctx(_row()), TENANT_ID, FILE_ID)

    patched["set_status"].assert_not_awaited()


async def test_api_rag_fora_do_ar_na_ultima_tentativa_vira_erro(patched) -> None:
    patched["copy"].side_effect = httpx.ConnectError("fora do ar")

    await copy_public_document(_ctx(_row(), job_try=kb_task.MAX_TRIES), TENANT_ID, FILE_ID)

    assert patched["set_status"].await_args.args[2] == "error"
