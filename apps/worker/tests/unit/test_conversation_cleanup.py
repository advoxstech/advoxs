import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from arq.worker import Retry

from app.tasks import conversation_cleanup

TENANT_ID = str(uuid.uuid4())
JOB_ID = str(uuid.uuid4())
CONVERSATION_ID = uuid.uuid4()


def _cleanup(attachments: list[str] | None = None):
    return SimpleNamespace(
        conversation_id=CONVERSATION_ID,
        message_ids=[str(uuid.uuid4())],
        attachment_document_ids=attachments or [],
        contact_phone_number="5511999998888",
    )


async def test_processa_limpeza_e_libera_mensagem_nova(monkeypatch) -> None:
    cleanup = _cleanup()
    claim = AsyncMock(return_value=True)
    load = AsyncMock(return_value=cleanup)
    delete_external = AsyncMock()
    finalize = AsyncMock(return_value=str(CONVERSATION_ID))
    enqueue_next = AsyncMock()
    monkeypatch.setattr(conversation_cleanup, "_claim_job", claim)
    monkeypatch.setattr(conversation_cleanup, "_load_cleanup", load)
    monkeypatch.setattr(conversation_cleanup, "_delete_external_state", delete_external)
    monkeypatch.setattr(conversation_cleanup, "_finalize_cleanup", finalize)

    import app.tasks.messages as messages

    monkeypatch.setattr(messages, "_enqueue_next_inbound_message_job", enqueue_next)

    await conversation_cleanup.process_conversation_cleanup({}, TENANT_ID, JOB_ID)

    delete_external.assert_awaited_once_with({}, TENANT_ID, cleanup)
    finalize.assert_awaited_once_with({}, TENANT_ID, JOB_ID, cleanup)
    enqueue_next.assert_awaited_once_with({}, str(CONVERSATION_ID))


async def test_falha_externa_mantem_job_para_nova_tentativa(monkeypatch) -> None:
    cleanup = _cleanup()
    monkeypatch.setattr(conversation_cleanup, "_claim_job", AsyncMock(return_value=True))
    monkeypatch.setattr(conversation_cleanup, "_load_cleanup", AsyncMock(return_value=cleanup))
    monkeypatch.setattr(
        conversation_cleanup,
        "_delete_external_state",
        AsyncMock(side_effect=httpx.ConnectError("fora do ar")),
    )
    release = AsyncMock(return_value=10)
    monkeypatch.setattr(conversation_cleanup, "_release_job", release)

    with pytest.raises(Retry):
        await conversation_cleanup.process_conversation_cleanup({"job_try": 1}, TENANT_ID, JOB_ID)

    release.assert_awaited_once()


async def test_limpeza_externa_remove_checkpoint_e_anexos() -> None:
    checkpoint_response = SimpleNamespace(raise_for_status=lambda: None)
    rag_response = SimpleNamespace(raise_for_status=lambda: None)
    http = SimpleNamespace(delete=AsyncMock(return_value=checkpoint_response))
    rag_http = SimpleNamespace(delete=AsyncMock(return_value=rag_response))
    cleanup = _cleanup([str(uuid.uuid4()), str(uuid.uuid4())])

    await conversation_cleanup._delete_external_state(
        {"http": http, "rag_http": rag_http}, TENANT_ID, cleanup
    )

    http.delete.assert_awaited_once()
    rag_http.delete.assert_awaited_once()
    assert (
        rag_http.delete.await_args.kwargs["params"]["docs_ids"] == cleanup.attachment_document_ids
    )


async def test_job_duplicado_ja_reservado_nao_faz_nada(monkeypatch) -> None:
    monkeypatch.setattr(conversation_cleanup, "_claim_job", AsyncMock(return_value=False))
    load = AsyncMock()
    monkeypatch.setattr(conversation_cleanup, "_load_cleanup", load)

    await conversation_cleanup.process_conversation_cleanup({}, TENANT_ID, JOB_ID)

    load.assert_not_awaited()
