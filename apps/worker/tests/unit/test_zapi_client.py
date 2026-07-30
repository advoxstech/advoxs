from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import app.clients.zapi as zapi_client
from app.clients.zapi import ZApiApiError, ZApiNetworkError, send_zapi_option_list, send_zapi_text_message


def _mock_async_client(monkeypatch, response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.post.return_value = response
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(zapi_client.httpx, "AsyncClient", MagicMock(return_value=cm))
    return client


def _response(status_code: int, json_body: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.is_error = status_code >= 400
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


class TestSendZApiTextMessage:
    async def test_envia_texto_com_sucesso(self, monkeypatch) -> None:
        response = _response(200, {"zaapId": "z1", "messageId": "m1", "id": "i1"})
        client = _mock_async_client(monkeypatch, response)

        await send_zapi_text_message(
            instance_id="inst-1", token="token-1", client_token=None, to="5511999998888", text="Olá"
        )

        client.post.assert_awaited_once()
        args, kwargs = client.post.call_args
        assert args[0] == "https://api.z-api.io/instances/inst-1/token/token-1/send-text"
        assert kwargs["json"] == {"phone": "5511999998888", "message": "Olá"}
        assert "Client-Token" not in kwargs["headers"]

    async def test_inclui_client_token_no_header_quando_presente(self, monkeypatch) -> None:
        response = _response(200, {})
        client = _mock_async_client(monkeypatch, response)

        await send_zapi_text_message(
            instance_id="inst-1",
            token="token-1",
            client_token="client-tok",
            to="5511999998888",
            text="Olá",
        )

        assert client.post.call_args.kwargs["headers"]["Client-Token"] == "client-tok"

    async def test_erro_de_rede_levanta_zapi_network_error(self, monkeypatch) -> None:
        client = AsyncMock()
        client.post.side_effect = httpx.ConnectError("down")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(zapi_client.httpx, "AsyncClient", MagicMock(return_value=cm))

        with pytest.raises(ZApiNetworkError):
            await send_zapi_text_message(
                instance_id="inst-1", token="token-1", client_token=None, to="5511999998888", text="Olá"
            )

    async def test_erro_http_levanta_zapi_api_error(self, monkeypatch) -> None:
        response = _response(400, {"error": "instância não encontrada"})
        _mock_async_client(monkeypatch, response)

        with pytest.raises(ZApiApiError, match="instância não encontrada"):
            await send_zapi_text_message(
                instance_id="inst-1", token="token-1", client_token=None, to="5511999998888", text="Olá"
            )


class TestSendZApiOptionList:
    async def test_envia_lista_com_sucesso(self, monkeypatch) -> None:
        response = _response(200, {"zaapId": "z1", "messageId": "m1", "id": "i1"})
        client = _mock_async_client(monkeypatch, response)
        options = [{"id": "Básico", "title": "Básico", "description": "R$ 49.90 = 500 créditos"}]

        await send_zapi_option_list(
            instance_id="inst-1",
            token="token-1",
            client_token=None,
            to="5511999998888",
            message="Escolha uma opção:",
            title="Pacotes de créditos",
            button_label="Ver opções",
            options=options,
        )

        client.post.assert_awaited_once()
        args, kwargs = client.post.call_args
        assert args[0] == "https://api.z-api.io/instances/inst-1/token/token-1/send-option-list"
        assert kwargs["json"] == {
            "phone": "5511999998888",
            "message": "Escolha uma opção:",
            "optionList": {
                "title": "Pacotes de créditos",
                "buttonLabel": "Ver opções",
                "options": options,
            },
        }

    async def test_erro_http_levanta_zapi_api_error(self, monkeypatch) -> None:
        response = _response(500, {})
        _mock_async_client(monkeypatch, response)

        with pytest.raises(ZApiApiError):
            await send_zapi_option_list(
                instance_id="inst-1",
                token="token-1",
                client_token=None,
                to="5511999998888",
                message="m",
                title="t",
                button_label="b",
                options=[],
            )

    async def test_erro_de_rede_levanta_zapi_network_error(self, monkeypatch) -> None:
        client = AsyncMock()
        client.post.side_effect = httpx.ConnectTimeout("timeout")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(zapi_client.httpx, "AsyncClient", MagicMock(return_value=cm))

        with pytest.raises(ZApiNetworkError):
            await send_zapi_option_list(
                instance_id="inst-1",
                token="token-1",
                client_token=None,
                to="5511999998888",
                message="m",
                title="t",
                button_label="b",
                options=[],
            )
