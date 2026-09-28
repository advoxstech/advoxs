from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import app.clients.media as media_client
from app.clients.media import (
    MediaDownloadError,
    MediaTooLargeError,
    download_meta_media,
    download_zapi_media,
)


def _mock_async_client(monkeypatch, client: AsyncMock) -> None:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(media_client.httpx, "AsyncClient", MagicMock(return_value=cm))


def _response(
    status_code: int = 200,
    json_body: dict | None = None,
    content: bytes = b"",
    headers: dict | None = None,
) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    response.headers = headers or {}

    async def chunks():
        yield content

    response.aiter_bytes = MagicMock(side_effect=chunks)
    if json_body is not None:
        response.json.return_value = json_body
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "erro", request=MagicMock(), response=response
        )
    else:
        response.raise_for_status.return_value = None
    return response


def _stream_response(client: AsyncMock, response: MagicMock) -> None:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)
    client.stream = MagicMock(return_value=cm)


class TestDownloadMetaMedia:
    async def test_baixa_com_sucesso_via_2_chamadas(self, monkeypatch) -> None:
        meta_response = _response(json_body={"url": "https://lookaside/xyz"})
        file_response = _response(content=b"%PDF-1.4")
        client = AsyncMock()
        client.get.return_value = meta_response
        _stream_response(client, file_response)
        _mock_async_client(monkeypatch, client)

        result = await download_meta_media("media-123", "token-abc")

        assert result == b"%PDF-1.4"
        first_call = client.get.call_args
        assert first_call.args[0].endswith("/media-123")
        assert first_call.kwargs["headers"]["Authorization"] == "Bearer token-abc"
        client.stream.assert_called_once_with(
            "GET",
            "https://lookaside/xyz",
            headers={"Authorization": "Bearer token-abc"},
        )

    async def test_interrompe_download_meta_acima_do_limite(self, monkeypatch) -> None:
        meta_response = _response(json_body={"url": "https://lookaside/xyz"})
        file_response = _response(content=b"12345")
        client = AsyncMock()
        client.get.return_value = meta_response
        _stream_response(client, file_response)
        _mock_async_client(monkeypatch, client)

        with pytest.raises(MediaTooLargeError):
            await download_meta_media("media-123", "token-abc", max_bytes=4)

    async def test_resposta_sem_url_levanta_media_download_error(self, monkeypatch) -> None:
        meta_response = _response(json_body={})
        client = AsyncMock()
        client.get.side_effect = [meta_response]
        _mock_async_client(monkeypatch, client)

        with pytest.raises(MediaDownloadError):
            await download_meta_media("media-123", "token-abc")

    async def test_erro_http_na_primeira_chamada_levanta_media_download_error(
        self, monkeypatch
    ) -> None:
        client = AsyncMock()
        client.get.side_effect = [_response(status_code=401)]
        _mock_async_client(monkeypatch, client)

        with pytest.raises(MediaDownloadError):
            await download_meta_media("media-123", "token-invalido")

    async def test_falha_de_rede_levanta_media_download_error(self, monkeypatch) -> None:
        client = AsyncMock()
        client.get.side_effect = httpx.ConnectError("down")
        _mock_async_client(monkeypatch, client)

        with pytest.raises(MediaDownloadError):
            await download_meta_media("media-123", "token-abc")


class TestDownloadZApiMedia:
    async def test_baixa_com_client_token_no_header(self, monkeypatch) -> None:
        client = AsyncMock()
        _stream_response(client, _response(content=b"conteudo"))
        _mock_async_client(monkeypatch, client)

        result = await download_zapi_media("https://z-api.example/media/x.pdf", "client-token-abc")

        assert result == b"conteudo"
        client.stream.assert_called_once_with(
            "GET",
            "https://z-api.example/media/x.pdf",
            headers={"Client-Token": "client-token-abc"},
        )

    async def test_sem_client_token_manda_sem_header_de_auth(self, monkeypatch) -> None:
        # Tenant sem Client-Token configurado (linha legada) — não quebra,
        # só não manda o header.
        client = AsyncMock()
        _stream_response(client, _response(content=b"conteudo"))
        _mock_async_client(monkeypatch, client)

        result = await download_zapi_media("https://z-api.example/media/x.pdf", None)

        assert result == b"conteudo"
        client.stream.assert_called_once_with(
            "GET", "https://z-api.example/media/x.pdf", headers={}
        )

    async def test_client_token_default_e_none(self, monkeypatch) -> None:
        client = AsyncMock()
        _stream_response(client, _response(content=b"conteudo"))
        _mock_async_client(monkeypatch, client)

        await download_zapi_media("https://z-api.example/media/x.pdf")

        client.stream.assert_called_once_with(
            "GET", "https://z-api.example/media/x.pdf", headers={}
        )

    async def test_erro_http_levanta_media_download_error(self, monkeypatch) -> None:
        client = AsyncMock()
        _stream_response(client, _response(status_code=404))
        _mock_async_client(monkeypatch, client)

        with pytest.raises(MediaDownloadError):
            await download_zapi_media("https://z-api.example/media/x.pdf", "client-token-abc")

    async def test_content_length_excessivo_para_antes_de_ler(self, monkeypatch) -> None:
        response = _response(content=b"nao deve ser lido", headers={"content-length": "999"})
        client = AsyncMock()
        _stream_response(client, response)
        _mock_async_client(monkeypatch, client)

        with pytest.raises(MediaTooLargeError):
            await download_zapi_media(
                "https://z-api.example/media/x.pdf", "client-token-abc", max_bytes=10
            )

        response.aiter_bytes.assert_not_called()
