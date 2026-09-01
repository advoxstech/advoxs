import io
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile

import app.services.test_attachments as test_attachments_module
from app.clients.rag import RagApiError
from app.services.test_attachments import process_test_attachment

TENANT_ID = "tenant-1"
CONVERSATION_ID = "tenant-1:teste-abc123"
MESSAGE_ID = "msg-1"


def _upload_file(filename: str, content: bytes = b"conteudo") -> UploadFile:
    return UploadFile(io.BytesIO(content), filename=filename)


@pytest.fixture
def ingest_mock(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(test_attachments_module, "insert_user_document", mock)
    return mock


async def test_formato_nao_suportado_nao_ingere(ingest_mock) -> None:
    result = await process_test_attachment(
        _upload_file("foto.jpg"),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
    )

    assert "formato não suportado" in result.lower()
    ingest_mock.assert_not_awaited()


async def test_pdf_ingere_com_sucesso(ingest_mock) -> None:
    result = await process_test_attachment(
        _upload_file("laudo.pdf", b"%PDF-1.4"),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
    )

    ingest_mock.assert_awaited_once()
    kwargs = ingest_mock.await_args.kwargs
    assert kwargs["tenant_id"] == TENANT_ID
    assert kwargs["conversation_id"] == CONVERSATION_ID
    assert kwargs["doc_id"] == MESSAGE_ID
    assert kwargs["filename"] == "laudo.pdf"
    assert kwargs["file_bytes"] == b"%PDF-1.4"
    assert "disponível para busca" in result


async def test_docx_e_txt_sao_suportados(ingest_mock) -> None:
    for filename in ["contrato.docx", "notas.txt"]:
        result = await process_test_attachment(
            _upload_file(filename),
            tenant_id=TENANT_ID,
            conversation_id=CONVERSATION_ID,
            message_id=MESSAGE_ID,
        )
        assert "disponível para busca" in result


async def test_arquivo_grande_demais_nao_ingere(ingest_mock, monkeypatch) -> None:
    monkeypatch.setattr(test_attachments_module.settings, "kb_max_file_size_bytes", 4)

    result = await process_test_attachment(
        _upload_file("laudo.pdf", b"conteudo grande"),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
    )

    assert "20 mb" in result.lower()
    ingest_mock.assert_not_awaited()


async def test_falha_na_ingestao_devolve_nota_de_erro_sem_levantar(ingest_mock) -> None:
    ingest_mock.side_effect = RagApiError("api_rag fora do ar")

    result = await process_test_attachment(
        _upload_file("laudo.pdf"),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
    )

    assert "processar o documento" in result.lower()


async def test_sem_filename_e_tratado_como_formato_nao_suportado(ingest_mock) -> None:
    result = await process_test_attachment(
        _upload_file(""),
        tenant_id=TENANT_ID,
        conversation_id=CONVERSATION_ID,
        message_id=MESSAGE_ID,
    )

    assert "formato não suportado" in result.lower()
    ingest_mock.assert_not_awaited()
