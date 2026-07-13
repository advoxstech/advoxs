from unittest.mock import MagicMock

import pytest

import clients.billing as billing_module
from clients.billing import criar_link_pagamento


class FakeAsyncClient:
    calls: list

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        FakeAsyncClient.calls.append((url, kwargs))
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"checkout_url": "https://checkout.stripe.com/pay/cs_1"}
        return response


@pytest.fixture(autouse=True)
def fake_httpx(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr(billing_module.httpx, "AsyncClient", FakeAsyncClient)


async def test_sucesso_retorna_checkout_url():
    url = await criar_link_pagamento("tenant-1", "5511999998888", "pkg-1")

    assert url == "https://checkout.stripe.com/pay/cs_1"
    (_, kwargs) = FakeAsyncClient.calls[0]
    assert kwargs["json"] == {
        "tenant_id": "tenant-1",
        "contact_phone_number": "5511999998888",
        "package_id": "pkg-1",
    }


async def test_falha_http_retorna_none(monkeypatch):
    class FailingClient(FakeAsyncClient):
        async def post(self, url, **kwargs):
            import httpx

            request = httpx.Request("POST", url)
            response = httpx.Response(502, request=request)
            raise httpx.HTTPStatusError("erro", request=request, response=response)

    monkeypatch.setattr(billing_module.httpx, "AsyncClient", FailingClient)

    assert await criar_link_pagamento("tenant-1", "5511999998888", "pkg-1") is None
