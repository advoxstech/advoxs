from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import HTTPStatusError, Request, Response

from app.clients.agents import send_message_to_agents

KWARGS = {
    "tenant_id": "t-1",
    "contact_phone_number": "5511888888888",
    "message": "Olá",
    "phone_number_id": "PNID",
    "access_token": "token",
}


def _http_returning(response: Response) -> AsyncMock:
    http = AsyncMock()
    http.post.return_value = response
    return http


async def test_returns_responses_and_tokens_on_200() -> None:
    response = MagicMock(spec=Response, status_code=200)
    response.json.return_value = {
        "responses": ["oi", "como posso ajudar?"],
        "tokens_used": 1234,
        "tokens_input": 1000,
        "tokens_output": 234,
    }
    http = _http_returning(response)

    result = await send_message_to_agents(http, **KWARGS)

    assert result == {
        "responses": ["oi", "como posso ajudar?"],
        "tokens_used": 1234,
        "tokens_input": 1000,
        "tokens_output": 234,
        "delivery_failures": [],
    }
    body = http.post.await_args.kwargs["json"]
    assert body["tenant_id"] == "t-1"
    assert body["access_token"] == "token"


async def test_resposta_sem_tokens_usa_zero() -> None:
    # Resposta do agents sem breakdown (versão antiga durante o deploy).
    response = MagicMock(spec=Response, status_code=200)
    response.json.return_value = {"responses": ["oi"]}
    http = _http_returning(response)

    result = await send_message_to_agents(http, **KWARGS)

    assert result == {
        "responses": ["oi"],
        "tokens_used": 0,
        "tokens_input": 0,
        "tokens_output": 0,
        "delivery_failures": [],
    }


async def test_resposta_com_delivery_failures() -> None:
    response = MagicMock(spec=Response, status_code=200)
    response.json.return_value = {
        "responses": ["oi", "tudo bem?"],
        "tokens_used": 500,
        "delivery_failures": [1],
    }
    http = _http_returning(response)

    result = await send_message_to_agents(http, **KWARGS)

    assert result["delivery_failures"] == [1]


async def test_returns_none_on_202_debounce() -> None:
    response = MagicMock(spec=Response, status_code=202)
    http = _http_returning(response)

    assert await send_message_to_agents(http, **KWARGS) is None


async def test_raises_on_5xx() -> None:
    request = Request("POST", "http://agents:8001/messages")
    response = Response(500, request=request)
    http = _http_returning(response)

    with pytest.raises(HTTPStatusError):
        await send_message_to_agents(http, **KWARGS)


async def test_inclui_agents_quando_informado() -> None:
    response = MagicMock(spec=Response, status_code=200)
    response.json.return_value = {"responses": ["oi"], "tokens_used": 100}
    http = _http_returning(response)
    agents = [
        {
            "id": "a1",
            "name": "Secretária",
            "instructions": "x",
            "is_entry_point": True,
            "knowledge_base_file_ids": [],
        }
    ]

    await send_message_to_agents(http, **KWARGS, agents=agents)

    body = http.post.await_args.kwargs["json"]
    assert body["agents"] == agents


async def test_sem_agents_manda_lista_vazia() -> None:
    response = MagicMock(spec=Response, status_code=200)
    response.json.return_value = {"responses": ["oi"], "tokens_used": 100}
    http = _http_returning(response)

    await send_message_to_agents(http, **KWARGS)

    body = http.post.await_args.kwargs["json"]
    assert body["agents"] == []
