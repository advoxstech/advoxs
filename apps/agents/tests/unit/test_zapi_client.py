import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

import clients.zapi as zapi_module
from clients.zapi import ZApiClient


@pytest.fixture
def client():
    return ZApiClient("inst-123", "token-do-tenant", None)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Backoff real deixaria os testes lentos — tempo não é o que testamos aqui."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


@pytest.fixture(autouse=True)
def rate_limit_sempre_libera(monkeypatch):
    """Testes de retry não são sobre rate limit — sem isso, dependeriam de um
    Redis real disponível e de um bucket compartilhado entre testes."""
    monkeypatch.setattr(zapi_module, "acquire_rate_limit_slot", AsyncMock(return_value=True))


class TestSendTextMessage:
    async def test_sucesso_monta_url_e_payload_corretos(self, client, monkeypatch) -> None:
        response = httpx.Response(200, json={"zaapId": "z1", "messageId": "m1", "id": "m1"})
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is True
        call = request_mock.await_args
        assert call.args[0] == "POST"
        assert call.args[1] == "https://api.z-api.io/instances/inst-123/token/token-do-tenant/send-text"
        assert call.kwargs["json"] == {"phone": "5511999998888", "message": "oi"}
        assert call.kwargs["headers"] == {"Content-Type": "application/json"}

    async def test_client_token_vai_no_header_quando_presente(self, monkeypatch) -> None:
        client_com_token = ZApiClient("inst-123", "token-do-tenant", "client-token-abc")
        response = httpx.Response(200, json={"id": "m1"})
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        await client_com_token.send_text_message("5511999998888", "oi")

        assert request_mock.await_args.kwargs["headers"]["Client-Token"] == "client-token-abc"

    async def test_sem_client_token_nao_inclui_header(self, client, monkeypatch) -> None:
        response = httpx.Response(200, json={"id": "m1"})
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        await client.send_text_message("5511999998888", "oi")

        assert "Client-Token" not in request_mock.await_args.kwargs["headers"]

    async def test_sucesso_na_primeira_tentativa_nao_faz_retry(self, client, monkeypatch) -> None:
        response = httpx.Response(200, json={"id": "m1"})
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is True
        assert request_mock.await_count == 1

    async def test_erro_4xx_nao_faz_retry(self, client, monkeypatch) -> None:
        response = httpx.Response(401, text="Unauthorized")
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is False
        assert request_mock.await_count == 1

    async def test_erro_5xx_faz_retry_e_se_recupera(self, client, monkeypatch) -> None:
        error_response = httpx.Response(503, text="indisponível")
        ok_response = httpx.Response(200, json={"id": "m2"})
        request_mock = AsyncMock(side_effect=[error_response, ok_response])
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is True
        assert request_mock.await_count == 2

    async def test_erro_5xx_esgota_as_tres_tentativas(self, client, monkeypatch) -> None:
        error_response = httpx.Response(500, text="internal error")
        request_mock = AsyncMock(return_value=error_response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is False
        assert request_mock.await_count == 3

    async def test_timeout_faz_retry_e_se_recupera_na_terceira_tentativa(
        self, client, monkeypatch
    ) -> None:
        ok_response = httpx.Response(200, json={"id": "m3"})
        request_mock = AsyncMock(
            side_effect=[
                httpx.TimeoutException("timeout"),
                httpx.TimeoutException("timeout"),
                ok_response,
            ]
        )
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is True
        assert request_mock.await_count == 3

    async def test_erro_de_conexao_esgota_as_tentativas(self, client, monkeypatch) -> None:
        request_mock = AsyncMock(side_effect=httpx.ConnectError("conexão recusada"))
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is False
        assert request_mock.await_count == 3


class TestSendTextMessageRateLimit:
    async def test_rate_limit_negado_uma_vez_ainda_tenta_de_novo(self, client, monkeypatch) -> None:
        ok_response = httpx.Response(200, json={"id": "m4"})
        request_mock = AsyncMock(return_value=ok_response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)
        acquire_mock = AsyncMock(side_effect=[False, True])
        monkeypatch.setattr(zapi_module, "acquire_rate_limit_slot", acquire_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is True
        assert acquire_mock.await_count == 2
        assert request_mock.await_count == 1

    async def test_rate_limit_negado_em_todas_as_tentativas_falha(self, client, monkeypatch) -> None:
        acquire_mock = AsyncMock(return_value=False)
        monkeypatch.setattr(zapi_module, "acquire_rate_limit_slot", acquire_mock)
        request_mock = AsyncMock()
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is False
        assert acquire_mock.await_count == 3
        request_mock.assert_not_awaited()


class TestSendDocumentMessage:
    async def test_monta_payload_de_documento(self, client, monkeypatch) -> None:
        response = httpx.Response(200, json={"id": "m3"})
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_document_message(
            "5511999998888", "https://exemplo.com/doc.pdf", filename="contrato.pdf"
        )

        assert result["success"] is True
        assert request_mock.await_args.kwargs["json"]["document"] == "https://exemplo.com/doc.pdf"
        assert request_mock.await_args.kwargs["json"]["fileName"] == "contrato.pdf"

    async def test_extensao_derivada_do_filename_vai_no_path(self, client, monkeypatch) -> None:
        response = httpx.Response(200, json={"id": "m3"})
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        await client.send_document_message(
            "5511999998888", "https://exemplo.com/arquivo-sem-extensao", filename="contrato.docx"
        )

        url = request_mock.await_args.args[1]
        assert url.endswith("/send-document/docx")

    async def test_extensao_derivada_do_link_quando_sem_filename(self, client, monkeypatch) -> None:
        response = httpx.Response(200, json={"id": "m3"})
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        await client.send_document_message("5511999998888", "https://exemplo.com/doc.xlsx")

        url = request_mock.await_args.args[1]
        assert url.endswith("/send-document/xlsx")

    async def test_sem_extensao_identificavel_cai_no_default_pdf(self, client, monkeypatch) -> None:
        response = httpx.Response(200, json={"id": "m3"})
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        await client.send_document_message("5511999998888", "https://exemplo.com/sem-extensao")

        url = request_mock.await_args.args[1]
        assert url.endswith("/send-document/pdf")

    async def test_caption_opcional_incluido_quando_presente(self, client, monkeypatch) -> None:
        response = httpx.Response(200, json={"id": "m3"})
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        await client.send_document_message(
            "5511999998888",
            "https://exemplo.com/doc.pdf",
            filename="contrato.pdf",
            caption="Segue o contrato",
        )

        assert request_mock.await_args.kwargs["json"]["caption"] == "Segue o contrato"

    async def test_erro_4xx_nao_faz_retry(self, client, monkeypatch) -> None:
        response = httpx.Response(400, text="Bad Request")
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_document_message("5511999998888", "https://exemplo.com/doc.pdf")

        assert result["success"] is False
        assert request_mock.await_count == 1
