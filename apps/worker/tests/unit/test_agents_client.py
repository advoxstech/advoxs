from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
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


async def test_inclui_end_customer_billing_quando_presente() -> None:
    response = MagicMock(spec=Response, status_code=200)
    response.json.return_value = {"responses": ["oi"], "tokens_used": 100}
    http = _http_returning(response)
    billing = {"enabled": True, "balance": 0, "packages": [{"id": "p-1", "name": "Básico"}]}

    await send_message_to_agents(http, **KWARGS, end_customer_billing=billing)

    body = http.post.await_args.kwargs["json"]
    assert body["end_customer_billing"] == billing


async def test_omite_end_customer_billing_quando_none() -> None:
    response = MagicMock(spec=Response, status_code=200)
    response.json.return_value = {"responses": ["oi"], "tokens_used": 100}
    http = _http_returning(response)

    await send_message_to_agents(http, **KWARGS)

    body = http.post.await_args.kwargs["json"]
    assert "end_customer_billing" not in body


async def test_balance_decimal_e_serializavel_de_verdade() -> None:
    """Regressão: end_customer_balances.credit_balance é Numeric(12,4) desde a
    Etapa 1/2 (moeda única) — vem como Decimal do banco. Um AsyncMock em
    `http.post` (como os testes acima usam) nunca chega a serializar o
    payload de verdade, mascarando TypeError: Object of type Decimal is not
    JSON serializable — só apareceu em produção. Aqui usamos um
    httpx.AsyncClient real com MockTransport pra forçar a codificação JSON
    de fato acontecer."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"responses": ["oi"], "tokens_used": 100})

    async with httpx.AsyncClient(
        base_url="http://agents-test", transport=httpx.MockTransport(handler)
    ) as http:
        billing = {"enabled": True, "balance": Decimal("120.5000"), "packages": []}
        result = await send_message_to_agents(http, **KWARGS, end_customer_billing=billing)

    assert result["responses"] == ["oi"]
