from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import app.clients.whatsapp as whatsapp_client
from app.clients.whatsapp import (
    WhatsAppSendError,
    send_interactive_list_message,
    send_text_message,
)


def _mock_async_client(monkeypatch, response: MagicMock) -> AsyncMock:
    client = AsyncMock()
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


class TestSendTextMessage:
    async def test_envia_texto_com_sucesso(self, monkeypatch) -> None:
        response = _response(200, {})
        client = _mock_async_client(monkeypatch, response)

        await send_text_message(
            phone_number_id="PNID", access_token="token", to="5511999998888", text="Olá"
        )

        client.post.assert_awaited_once()
        args, kwargs = client.post.call_args
        assert args[0] == "https://graph.facebook.com/v23.0/PNID/messages"
        assert kwargs["json"]["type"] == "text"
        assert kwargs["json"]["text"]["body"] == "Olá"
        assert kwargs["headers"]["Authorization"] == "Bearer token"

    async def test_erro_da_graph_api_levanta_whatsapp_send_error(self, monkeypatch) -> None:
        response = _response(400, {"error": {"message": "token inválido"}})
        _mock_async_client(monkeypatch, response)

        with pytest.raises(WhatsAppSendError):
            await send_text_message(
                phone_number_id="PNID", access_token="token", to="5511999998888", text="Olá"
            )

    async def test_falha_de_rede_levanta_whatsapp_send_error(self, monkeypatch) -> None:
        client = AsyncMock()
        client.post.side_effect = httpx.ConnectError("down")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(whatsapp_client.httpx, "AsyncClient", MagicMock(return_value=cm))

        with pytest.raises(WhatsAppSendError):
            await send_text_message(
                phone_number_id="PNID", access_token="token", to="5511999998888", text="Olá"
            )


class TestSendInteractiveListMessage:
    async def test_envia_lista_com_secoes(self, monkeypatch) -> None:
        response = _response(200, {})
        client = _mock_async_client(monkeypatch, response)

        await send_interactive_list_message(
            phone_number_id="PNID",
            access_token="token",
            to="5511999998888",
            header="Pacotes",
            body="Escolha um:",
            sections=[
                {
                    "title": "Disponíveis",
                    "rows": [{"id": "Básico", "title": "Básico", "description": "R$ 49,90"}],
                }
            ],
        )

        client.post.assert_awaited_once()
        _, kwargs = client.post.call_args
        payload = kwargs["json"]
        assert payload["type"] == "interactive"
        assert payload["interactive"]["type"] == "list"
        assert payload["interactive"]["header"] == {"type": "text", "text": "Pacotes"}
        assert payload["interactive"]["body"] == {"text": "Escolha um:"}
        assert payload["interactive"]["action"]["sections"][0]["rows"][0]["id"] == "Básico"

    async def test_erro_da_graph_api_levanta_whatsapp_send_error(self, monkeypatch) -> None:
        response = _response(500, {})
        _mock_async_client(monkeypatch, response)

        with pytest.raises(WhatsAppSendError):
            await send_interactive_list_message(
                phone_number_id="PNID",
                access_token="token",
                to="5511999998888",
                header="Pacotes",
                body="Escolha um:",
                sections=[{"title": "x", "rows": []}],
            )
