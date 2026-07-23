import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import app.clients.billing as billing_client
from app.clients.billing import BillingCheckoutError, create_end_customer_checkout
from app.config import settings

TENANT_ID = str(uuid.uuid4())
PACKAGE_ID = str(uuid.uuid4())


def _mock_async_client(monkeypatch, response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.post.return_value = response
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(billing_client.httpx, "AsyncClient", MagicMock(return_value=cm))
    return client


def _response(status_code: int, json_body: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.is_error = status_code >= 400
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


class TestCreateEndCustomerCheckout:
    async def test_chama_endpoint_interno_e_devolve_url(self, monkeypatch) -> None:
        # internal_service_key vem vazio por padrão no ambiente de teste (sem
        # .env local) — fixa um valor não-vazio pra exercitar de fato o envio
        # do header (o client só manda "Authorization" quando a key não é
        # vazia, ver create_end_customer_checkout).
        monkeypatch.setattr(settings, "internal_service_key", "chave-interna-de-teste")
        response = _response(200, {"checkout_url": "https://checkout.stripe.com/xyz"})
        client = _mock_async_client(monkeypatch, response)

        url = await create_end_customer_checkout(
            tenant_id=TENANT_ID, contact_phone_number="5511999998888", package_id=PACKAGE_ID
        )

        assert url == "https://checkout.stripe.com/xyz"
        client.post.assert_awaited_once()
        args, kwargs = client.post.call_args
        assert args[0] == "/api/v1/internal/end-customer-billing/checkout"
        assert kwargs["json"] == {
            "tenant_id": TENANT_ID,
            "contact_phone_number": "5511999998888",
            "package_id": PACKAGE_ID,
        }
        assert kwargs["headers"]["Authorization"] == settings.internal_service_key

    async def test_erro_do_endpoint_levanta_billing_checkout_error(self, monkeypatch) -> None:
        response = _response(400, {"detail": "Pacote de créditos inválido"})
        _mock_async_client(monkeypatch, response)

        with pytest.raises(BillingCheckoutError):
            await create_end_customer_checkout(
                tenant_id=TENANT_ID, contact_phone_number="5511999998888", package_id=PACKAGE_ID
            )

    async def test_falha_de_rede_levanta_billing_checkout_error(self, monkeypatch) -> None:
        client = AsyncMock()
        client.post.side_effect = httpx.ConnectError("down")
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(billing_client.httpx, "AsyncClient", MagicMock(return_value=cm))

        with pytest.raises(BillingCheckoutError):
            await create_end_customer_checkout(
                tenant_id=TENANT_ID, contact_phone_number="5511999998888", package_id=PACKAGE_ID
            )
