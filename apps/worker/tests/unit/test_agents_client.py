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
    response.json.return_value = {"responses": ["oi", "como posso ajudar?"], "tokens_used": 1234}
    http = _http_returning(response)

    result = await send_message_to_agents(http, **KWARGS)

    assert result == {
        "responses": ["oi", "como posso ajudar?"],
        "tokens_used": 1234,
        "delivery_failures": [],
    }
    body = http.post.await_args.kwargs["json"]
    assert body["tenant_id"] == "t-1"
    assert body["access_token"] == "token"


async def test_resposta_sem_tokens_usa_zero() -> None:
    response = MagicMock(spec=Response, status_code=200)
    response.json.return_value = {"responses": ["oi"]}
    http = _http_returning(response)

    result = await send_message_to_agents(http, **KWARGS)

    assert result == {"responses": ["oi"], "tokens_used": 0, "delivery_failures": []}


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
