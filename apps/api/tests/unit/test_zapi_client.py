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
