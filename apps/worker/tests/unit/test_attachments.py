from unittest.mock import AsyncMock

import httpx
import pytest

import app.tasks.attachments as attachments_task
from app.clients.media import MediaDownloadError, MediaTooLargeError
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
        "ingest": AsyncMock(return_value={}),
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

    patched["download_meta"].assert_awaited_once_with(
        "media-123", "token-abc", settings.attachment_max_bytes
    )
    patched["ingest"].assert_awaited_once()
    kwargs = patched["ingest"].await_args.kwargs
    assert kwargs["tenant_id"] == TENANT_ID
    assert kwargs["doc_id"] == MESSAGE_ID
    assert kwargs["conversation_id"] == CONVERSATION_ID
    assert kwargs["filename"] == f"anexo-{MESSAGE_ID}.pdf"
    assert kwargs["file_bytes"] == b"%PDF-1.4"
    assert result is not None
    assert "disponível para busca" in result


async def test_pdf_com_leitura_parcial_avisa_o_agente(patched) -> None:
    patched["ingest"].return_value = {"pages_total": 50, "partial_pages": [12, 18]}

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

    assert "processado parcialmente" in result
    assert "páginas 12, 18" in result
    assert "não invente" in result


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
        "https://z-api.example/media/x.docx",
        "client-token-abc",
        settings.attachment_max_bytes,
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
    patched["download_meta"].side_effect = MediaTooLargeError("grande")

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
    assert "excede o limite" in result.lower()
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


async def _process(patched) -> str | None:
    return await process_inbound_attachment(
        AsyncMock(),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
        media_ref="media-123",
        media_type="application/pdf",
        whatsapp_provider="meta",
        access_token="token",
    )


@pytest.mark.parametrize(
    ("motivo", "trecho"),
    [
        ("senha", "protegido por senha"),
        ("corrompido", "corrompido ou danificado"),
        ("paginas_demais", "mais de 3.000 páginas"),
        ("escaneadas_demais", "digitalizado"),
        ("sem_texto", "qualidade ruim"),
    ],
)
async def test_recusa_do_api_rag_vira_aviso_com_o_motivo(patched, motivo, trecho) -> None:
    request = httpx.Request("POST", "http://rag")
    patched["ingest"].side_effect = httpx.HTTPStatusError(
        "400",
        request=request,
        response=httpx.Response(400, json={"detail": "texto", "motivo": motivo}, request=request),
    )

    result = await _process(patched)

    assert trecho in result
    assert result.startswith("[Anexo recebido mas não pôde ser lido")


async def test_recusa_sem_motivo_conhecido_continua_generica(patched) -> None:
    request = httpx.Request("POST", "http://rag")
    patched["ingest"].side_effect = httpx.HTTPStatusError(
        "400", request=request, response=httpx.Response(400, json={"detail": "x"}, request=request)
    )

    assert "falha ao processar o documento" in await _process(patched)


async def test_leitura_que_passa_do_prazo_vira_aviso_de_qualidade(patched) -> None:
    patched["ingest"].side_effect = httpx.ReadTimeout("prazo")

    result = await _process(patched)

    assert "não terminou no tempo limite" in result


async def test_documento_lido_como_imagem_avisa_sobre_carimbo(patched) -> None:
    patched["ingest"].return_value = {"pages_total": 10, "ocr_pages": 10, "partial_pages": []}

    result = await _process(patched)

    assert result.startswith("[Documento recebido e processado:")
    assert "carimbo" in result


async def test_documento_de_texto_nao_ganha_aviso_de_digitalizado(patched) -> None:
    patched["ingest"].return_value = {"pages_total": 10, "ocr_pages": 0, "partial_pages": []}

    assert "carimbo" not in await _process(patched)


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


# --- leitura demorada: aviso ao contato -----------------------------------------


async def _slow_ingest(*_args, **_kwargs):
    import asyncio

    await asyncio.sleep(0.2)
    return {"pages_total": 300, "partial_pages": []}


def _pdf_kwargs(on_slow):
    return dict(
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
        media_ref="media-123",
        media_type="application/pdf",
        whatsapp_provider="meta",
        access_token="token",
        on_slow_reading=on_slow,
    )


async def test_leitura_rapida_nao_avisa_e_usa_prazo_longo(patched) -> None:
    on_slow = AsyncMock()

    result = await process_inbound_attachment(AsyncMock(), **_pdf_kwargs(on_slow))

    on_slow.assert_not_awaited()
    assert "processado" in result
    assert patched["ingest"].await_args.kwargs["timeout"] == (
        attachments_task.ATTACHMENT_RAG_TIMEOUT_SECONDS
    )


async def test_leitura_demorada_avisa_uma_vez_e_espera_o_fim(patched, monkeypatch) -> None:
    monkeypatch.setattr(attachments_task, "ATTACHMENT_NOTICE_AFTER_SECONDS", 0.01)
    monkeypatch.setattr(attachments_task, "ingest_document", _slow_ingest)
    on_slow = AsyncMock()

    result = await process_inbound_attachment(AsyncMock(), **_pdf_kwargs(on_slow))

    on_slow.assert_awaited_once()
    assert "disponível para busca" in result


async def test_falha_no_aviso_nao_interrompe_a_leitura(patched, monkeypatch) -> None:
    monkeypatch.setattr(attachments_task, "ATTACHMENT_NOTICE_AFTER_SECONDS", 0.01)
    monkeypatch.setattr(attachments_task, "ingest_document", _slow_ingest)
    on_slow = AsyncMock(side_effect=RuntimeError("whatsapp fora"))

    result = await process_inbound_attachment(AsyncMock(), **_pdf_kwargs(on_slow))

    assert "disponível para busca" in result


async def test_cancelar_a_espera_cancela_a_leitura() -> None:
    import asyncio

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    waiting = asyncio.ensure_future(
        attachments_task.await_with_notice(work(), None, tenant_id=TENANT_ID, notice_after=5)
    )
    await started.wait()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert cancelled.is_set()
