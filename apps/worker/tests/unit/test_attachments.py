from unittest.mock import AsyncMock

import httpx
import pytest

import app.tasks.attachments as attachments_task
from app.clients.media import MediaDownloadError
from app.config import settings
from app.tasks.attachments import process_inbound_attachment

# conversation_id passado a process_inbound_attachment é só o contato (ver
# comentário em app/tasks/messages.py sobre por que NUNCA pode ser o
# thread_id composto "{tenant_id}:{contact_phone_number}").
TENANT_ID = "tenant-1"
CONVERSATION_ID = "5511999998888"
MESSAGE_ID = "msg-1"


@pytest.fixture
def patched(monkeypatch):
    mocks = {
        "download_meta": AsyncMock(return_value=b"%PDF-1.4"),
        "download_zapi": AsyncMock(return_value=b"%PDF-1.4"),
        "ingest": AsyncMock(),
    }
    monkeypatch.setattr(attachments_task, "download_meta_media", mocks["download_meta"])
    monkeypatch.setattr(attachments_task, "download_zapi_media", mocks["download_zapi"])
    monkeypatch.setattr(attachments_task, "ingest_document", mocks["ingest"])
    return mocks


async def test_sem_anexo_devolve_none(patched) -> None:
    result = await process_inbound_attachment(
        AsyncMock(),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
        media_ref=None,
        media_type=None,
        whatsapp_provider="meta",
        access_token="token",
    )

    assert result is None
    patched["download_meta"].assert_not_awaited()
    patched["ingest"].assert_not_awaited()


async def test_formato_nao_suportado_nao_baixa_nem_ingere(patched) -> None:
    result = await process_inbound_attachment(
        AsyncMock(),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
        media_ref="media-123",
        media_type="image/jpeg",
        whatsapp_provider="meta",
        access_token="token",
    )

    assert result is not None
    assert "formato não suportado" in result.lower()
    patched["download_meta"].assert_not_awaited()
    patched["ingest"].assert_not_awaited()


async def test_pdf_via_meta_baixa_e_ingere_com_sucesso(patched) -> None:
    result = await process_inbound_attachment(
        AsyncMock(),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
        media_ref="media-123",
        media_type="application/pdf",
        whatsapp_provider="meta",
        access_token="token-abc",
    )

    patched["download_meta"].assert_awaited_once_with("media-123", "token-abc")
    patched["ingest"].assert_awaited_once()
    kwargs = patched["ingest"].await_args.kwargs
    assert kwargs["tenant_id"] == TENANT_ID
    assert kwargs["doc_id"] == MESSAGE_ID
    assert kwargs["conversation_id"] == CONVERSATION_ID
    assert kwargs["filename"] == f"anexo-{MESSAGE_ID}.pdf"
    assert kwargs["file_bytes"] == b"%PDF-1.4"
    assert result is not None
    assert "disponível para busca" in result


async def test_docx_via_zapi_usa_download_zapi(patched) -> None:
    result = await process_inbound_attachment(
        AsyncMock(),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
        media_ref="https://z-api.example/media/x.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        whatsapp_provider="zapi",
        access_token=None,
        zapi_client_token="client-token-abc",
    )

    patched["download_zapi"].assert_awaited_once_with(
        "https://z-api.example/media/x.docx", "client-token-abc"
    )
    patched["download_meta"].assert_not_awaited()
    kwargs = patched["ingest"].await_args.kwargs
    assert kwargs["filename"] == f"anexo-{MESSAGE_ID}.docx"
    assert result is not None


async def test_falha_no_download_devolve_nota_de_erro_sem_levantar(patched) -> None:
    patched["download_meta"].side_effect = MediaDownloadError("falhou")

    result = await process_inbound_attachment(
        AsyncMock(),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
        media_ref="media-123",
        media_type="application/pdf",
        whatsapp_provider="meta",
        access_token="token",
    )

    assert result is not None
    assert "baixar" in result.lower()
    patched["ingest"].assert_not_awaited()


async def test_arquivo_grande_demais_nao_ingere(patched, monkeypatch) -> None:
    monkeypatch.setattr(settings, "attachment_max_bytes", 4)

    result = await process_inbound_attachment(
        AsyncMock(),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
        media_ref="media-123",
        media_type="application/pdf",
        whatsapp_provider="meta",
        access_token="token",
    )

    assert result is not None
    assert "20 mb" in result.lower()
    patched["ingest"].assert_not_awaited()


async def test_falha_na_ingestao_devolve_nota_de_erro_sem_levantar(patched) -> None:
    patched["ingest"].side_effect = httpx.HTTPStatusError(
        "500", request=httpx.Request("POST", "http://rag"), response=httpx.Response(500)
    )

    result = await process_inbound_attachment(
        AsyncMock(),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
        media_ref="media-123",
        media_type="application/pdf",
        whatsapp_provider="meta",
        access_token="token",
    )

    assert result is not None
    assert "processar o documento" in result.lower()


async def test_txt_e_formato_suportado(patched) -> None:
    result = await process_inbound_attachment(
        AsyncMock(),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
        media_ref="media-123",
        media_type="text/plain",
        whatsapp_provider="meta",
        access_token="token",
    )

    kwargs = patched["ingest"].await_args.kwargs
    assert kwargs["filename"] == f"anexo-{MESSAGE_ID}.txt"
    assert result is not None
