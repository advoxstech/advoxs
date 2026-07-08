from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import app.clients.whatsapp as whatsapp_client
from app.clients.whatsapp import (
    WhatsAppApiError,
    WhatsAppNetworkError,
    fetch_display_phone_number,
    register_number,
)


def _mock_async_client(monkeypatch, response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.get.return_value = response
    client.post.return_value = response
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(whatsapp_client.httpx, "AsyncClient", MagicMock(return_value=cm))
    return client


def _response(status_code: int, json_body: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.is_error = status_code >= 400
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


class TestFetchDisplayPhoneNumber:
    async def test_sucesso_retorna_numero(self, monkeypatch) -> None:
        response = _response(200, {"display_phone_number": "+5511987654321"})
        client = _mock_async_client(monkeypatch, response)

        result = await fetch_display_phone_number("PNID", "token-claro")

        assert result == "+5511987654321"
        client.get.assert_awaited_once()
        _, kwargs = client.get.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer token-claro"
        assert kwargs["params"]["fields"] == "display_phone_number"

    async def test_erro_da_meta_levanta_whatsapp_api_error(self, monkeypatch) -> None:
        response = _response(400, {"error": {"message": "Token inválido"}})
        _mock_async_client(monkeypatch, response)

        with pytest.raises(WhatsAppApiError, match="Token inválido"):
            await fetch_display_phone_number("PNID", "token-claro")

    async def test_erro_sem_corpo_json_usa_mensagem_padrao(self, monkeypatch) -> None:
        response = _response(500, {})
        response.json.side_effect = ValueError("no json")
        _mock_async_client(monkeypatch, response)

        with pytest.raises(WhatsAppApiError, match="Não foi possível validar"):
            await fetch_display_phone_number("PNID", "token-claro")

    async def test_falha_de_rede_levanta_whatsapp_network_error(self, monkeypatch) -> None:
        client = AsyncMock()
        client.get.side_effect = httpx.ConnectError("down")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(whatsapp_client.httpx, "AsyncClient", MagicMock(return_value=cm))

        with pytest.raises(WhatsAppNetworkError):
            await fetch_display_phone_number("PNID", "token-claro")


class TestRegisterNumber:
    async def test_sucesso_nao_levanta_e_envia_payload_correto(self, monkeypatch) -> None:
        response = _response(200, {"success": True})
        client = _mock_async_client(monkeypatch, response)

        await register_number("PNID", "token-claro", "123456")

        client.post.assert_awaited_once()
        _, kwargs = client.post.call_args
        assert kwargs["json"] == {"messaging_product": "whatsapp", "pin": "123456"}
        assert kwargs["headers"]["Authorization"] == "Bearer token-claro"

    async def test_pin_incorreto_levanta_whatsapp_api_error(self, monkeypatch) -> None:
        response = _response(400, {"error": {"message": "PIN incorreto"}})
        _mock_async_client(monkeypatch, response)

        with pytest.raises(WhatsAppApiError, match="PIN incorreto"):
            await register_number("PNID", "token-claro", "123456")

    async def test_falha_de_rede_levanta_whatsapp_network_error(self, monkeypatch) -> None:
        client = AsyncMock()
        client.post.side_effect = httpx.ConnectError("down")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(whatsapp_client.httpx, "AsyncClient", MagicMock(return_value=cm))

        with pytest.raises(WhatsAppNetworkError):
            await register_number("PNID", "token-claro", "123456")
