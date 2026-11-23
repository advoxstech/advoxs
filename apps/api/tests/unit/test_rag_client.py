from unittest.mock import AsyncMock

import httpx
import pytest

from app.clients.rag import RagApiError, insert_user_document, insert_user_document_reading


class TestInsertUserDocument:
    async def test_envia_multipart_com_os_campos_certos(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"ok": True})
        post_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "post", post_mock)

        await insert_user_document(
            tenant_id="tenant-1",
            conversation_id="teste-abc123",
            doc_id="msg-1",
            filename="laudo.pdf",
            file_bytes=b"%PDF-1.4",
        )

        post_mock.assert_awaited_once()
        args, kwargs = post_mock.call_args
        assert args[0] == "/documents/users/insert"
        assert kwargs["data"] == {
            "tenant_id": "tenant-1",
            "conversation_id": "teste-abc123",
            "doc_id": "msg-1",
        }
        assert kwargs["files"]["file"][0] == "laudo.pdf"
        assert kwargs["files"]["file"][1] == b"%PDF-1.4"

    async def test_erro_http_levanta_rag_api_error(self, monkeypatch) -> None:
        response = httpx.Response(400, text="formato inválido")
        monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))

        with pytest.raises(RagApiError):
            await insert_user_document(
                tenant_id="tenant-1",
                conversation_id="teste-abc123",
                doc_id="msg-1",
                filename="laudo.pdf",
                file_bytes=b"%PDF-1.4",
            )

    async def test_falha_de_rede_levanta_rag_api_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ConnectError("indisponível"))
        )

        with pytest.raises(RagApiError):
            await insert_user_document(
                tenant_id="tenant-1",
                conversation_id="teste-abc123",
                doc_id="msg-1",
                filename="laudo.pdf",
                file_bytes=b"%PDF-1.4",
            )


class TestInsertUserDocumentReading:
    async def test_recusa_traz_o_motivo(self, monkeypatch) -> None:
        response = httpx.Response(400, json={"detail": "Tem senha.", "motivo": "senha"})
        monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))

        with pytest.raises(RagApiError) as erro:
            await insert_user_document_reading(
                tenant_id="t", conversation_id="c", doc_id="d", filename="a.pdf", file_bytes=b"x"
            )

        assert erro.value.motivo == "senha"

    async def test_prazo_estourado_e_leitura_demorada(self, monkeypatch) -> None:
        monkeypatch.setattr(
            httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ReadTimeout("prazo"))
        )

        with pytest.raises(RagApiError) as erro:
            await insert_user_document_reading(
                tenant_id="t", conversation_id="c", doc_id="d", filename="a.pdf", file_bytes=b"x"
            )

        assert erro.value.motivo == "leitura_demorada"

    async def test_sucesso_devolve_paginas_parciais_e_ocr(self, monkeypatch) -> None:
        response = httpx.Response(
            200, json={"leitura": {"paginas": 9, "ocr": 9, "paginas_parciais": [4, 2, "x"]}}
        )
        monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))

        reading = await insert_user_document_reading(
            tenant_id="t", conversation_id="c", doc_id="d", filename="a.pdf", file_bytes=b"x"
        )

        assert reading == {"partial_pages": [2, 4], "ocr_pages": 9}
        assert await insert_user_document(
            tenant_id="t", conversation_id="c", doc_id="d", filename="a.pdf", file_bytes=b"x"
        ) == [2, 4]
