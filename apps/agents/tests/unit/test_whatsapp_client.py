import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

import clients.whatsapp as whatsapp_module
from clients.whatsapp import WhatsAppClient


@pytest.fixture
def client():
    return WhatsAppClient("111222333", "token-do-tenant")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Backoff real deixaria os testes lentos — tempo não é o que testamos aqui."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


@pytest.fixture(autouse=True)
def rate_limit_sempre_libera(monkeypatch):
    """Testes de retry não são sobre rate limit — sem isso, dependeriam de um
    Redis real disponível e de um bucket compartilhado entre testes.
    `TestSendTextMessageRateLimit` sobrescreve este mock nos seus próprios testes."""
    monkeypatch.setattr(whatsapp_module, "acquire_rate_limit_slot", AsyncMock(return_value=True))


class TestSendTextMessageRetry:
    async def test_sucesso_na_primeira_tentativa_nao_faz_retry(self, client, monkeypatch) -> None:
        response = httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is True
        assert request_mock.await_count == 1

    async def test_erro_4xx_nao_faz_retry(self, client, monkeypatch) -> None:
        response = httpx.Response(401, text='{"error":"Invalid OAuth access token"}')
        request_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is False
        assert request_mock.await_count == 1

    async def test_erro_5xx_faz_retry_e_se_recupera_na_segunda_tentativa(
        self, client, monkeypatch
    ) -> None:
        error_response = httpx.Response(503, text="service unavailable")
        ok_response = httpx.Response(200, json={"messages": [{"id": "wamid.2"}]})
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
        ok_response = httpx.Response(200, json={"messages": [{"id": "wamid.3"}]})
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
        ok_response = httpx.Response(200, json={"messages": [{"id": "wamid.4"}]})
        request_mock = AsyncMock(return_value=ok_response)
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)
        acquire_mock = AsyncMock(side_effect=[False, True])
        monkeypatch.setattr(whatsapp_module, "acquire_rate_limit_slot", acquire_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is True
        assert acquire_mock.await_count == 2
        assert request_mock.await_count == 1

    async def test_rate_limit_negado_em_todas_as_tentativas_falha(self, client, monkeypatch) -> None:
        acquire_mock = AsyncMock(return_value=False)
        monkeypatch.setattr(whatsapp_module, "acquire_rate_limit_slot", acquire_mock)
        request_mock = AsyncMock()
        monkeypatch.setattr(httpx.AsyncClient, "request", request_mock)

        result = await client.send_text_message("5511999998888", "oi")

        assert result["success"] is False
        assert acquire_mock.await_count == 3
        request_mock.assert_not_awaited()
