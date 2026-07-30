from unittest.mock import AsyncMock

import httpx
import pytest

from app.clients.zapi import (
    ZApiApiError,
    ZApiNetworkError,
    check_zapi_status,
    configure_zapi_webhook,
    disconnect_zapi_instance,
    fetch_zapi_connected_phone,
    fetch_zapi_qrcode,
    send_zapi_text_message,
)


class TestCheckZApiStatus:
    async def test_retorna_o_corpo_da_resposta(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"connected": False, "smartphoneConnected": False})
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=response))

        result = await check_zapi_status("inst-1", "token-1", None)

        assert result == {"connected": False, "smartphoneConnected": False}

    async def test_erro_de_rede_levanta_zapi_network_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            httpx.AsyncClient, "get", AsyncMock(side_effect=httpx.ConnectError("falhou"))
        )

        with pytest.raises(ZApiNetworkError):
            await check_zapi_status("inst-1", "token-1", None)

    async def test_erro_http_levanta_zapi_api_error(self, monkeypatch) -> None:
        response = httpx.Response(401, text="Unauthorized")
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=response))

        with pytest.raises(ZApiApiError):
            await check_zapi_status("inst-1", "token-1", None)


class TestConfigureZApiWebhook:
    async def test_chama_o_endpoint_de_webhook_com_a_url(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"value": True})
        post_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "post", post_mock)

        await configure_zapi_webhook(
            "inst-1", "token-1", None, "https://exemplo.com/webhook/segredo"
        )

        assert post_mock.await_count == 1


class TestFetchZApiQrcode:
    async def test_retorna_a_imagem_base64(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"value": "data:image/png;base64,AAAA"})
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=response))

        result = await fetch_zapi_qrcode("inst-1", "token-1", None)

        assert "AAAA" in result


class TestFetchZApiConnectedPhone:
    async def test_retorna_o_telefone_quando_presente(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"phone": "5511999998888"})
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=response))

        result = await fetch_zapi_connected_phone("inst-1", "token-1", None)

        assert result == "5511999998888"

    async def test_retorna_none_quando_ausente(self, monkeypatch) -> None:
        response = httpx.Response(200, json={})
        monkeypatch.setattr(httpx.AsyncClient, "get", AsyncMock(return_value=response))

        result = await fetch_zapi_connected_phone("inst-1", "token-1", None)

        assert result is None


class TestDisconnectZApiInstance:
    async def test_chama_o_endpoint_de_disconnect(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"value": True})
        post_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "post", post_mock)

        await disconnect_zapi_instance("inst-1", "token-1", None)

        assert post_mock.await_count == 1


class TestSendZApiTextMessage:
    async def test_envia_texto_com_sucesso(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"zaapId": "abc", "messageId": "123"})
        post_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "post", post_mock)

        await send_zapi_text_message("inst-1", "token-1", None, "5511999998888", "Olá!")

        assert post_mock.await_count == 1
        _, kwargs = post_mock.call_args
        assert kwargs["json"] == {"phone": "5511999998888", "message": "Olá!"}
        assert post_mock.call_args.args[0].endswith("/instances/inst-1/token/token-1/send-text")

    async def test_inclui_client_token_no_header_quando_presente(self, monkeypatch) -> None:
        response = httpx.Response(200, json={"messageId": "123"})
        post_mock = AsyncMock(return_value=response)
        monkeypatch.setattr(httpx.AsyncClient, "post", post_mock)

        await send_zapi_text_message("inst-1", "token-1", "client-token-1", "5511999998888", "Oi")

        assert post_mock.call_args.kwargs["headers"]["Client-Token"] == "client-token-1"

    async def test_erro_de_rede_levanta_zapi_network_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ConnectError("falhou"))
        )

        with pytest.raises(ZApiNetworkError):
            await send_zapi_text_message("inst-1", "token-1", None, "5511999998888", "Oi")

    async def test_erro_http_levanta_zapi_api_error_com_mensagem_do_corpo(
        self, monkeypatch
    ) -> None:
        response = httpx.Response(400, json={"error": "número inválido"})
        monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))

        with pytest.raises(ZApiApiError, match="número inválido"):
            await send_zapi_text_message("inst-1", "token-1", None, "5511999998888", "Oi")

    async def test_erro_http_sem_corpo_json_usa_mensagem_padrao(self, monkeypatch) -> None:
        response = httpx.Response(500, text="internal error")
        monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))

        with pytest.raises(ZApiApiError, match="Não foi possível enviar a mensagem pela Z-API"):
            await send_zapi_text_message("inst-1", "token-1", None, "5511999998888", "Oi")
