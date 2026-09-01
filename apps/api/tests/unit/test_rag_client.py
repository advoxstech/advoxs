from unittest.mock import AsyncMock

import httpx
import pytest

from app.clients.rag import RagApiError, insert_user_document


class TestInsertUserDocument:
    async def test_envia_multipart_com_os_campos_certos(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"ok": True})
        post_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "post", post_mock)

        await insert_user_document(
            tenant_id="tenant-1",
            conversation_id="tenant-1:teste-abc123",
            doc_id="msg-1",
            filename="laudo.pdf",
            file_bytes=b"%PDF-1.4",
        )

        post_mock.assert_awaited_once()
        args, kwargs = post_mock.call_args
        assert args[0] == "/documents/users/insert"
        assert kwargs["data"] == {
            "tenant_id": "tenant-1",
            "conversation_id": "tenant-1:teste-abc123",
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
                conversation_id="tenant-1:teste-abc123",
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
                conversation_id="tenant-1:teste-abc123",
                doc_id="msg-1",
                filename="laudo.pdf",
                file_bytes=b"%PDF-1.4",
            )
