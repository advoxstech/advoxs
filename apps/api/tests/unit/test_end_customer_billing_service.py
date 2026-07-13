import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.end_customer_billing as service
from app.services.end_customer_billing import (
    BillingNotConfiguredError,
    InvalidPackageError,
    StripeApiError,
    create_end_customer_checkout_session,
)

TENANT_ID = uuid.uuid4()
PACKAGE_ID = uuid.uuid4()
CONTACT = "5511999998888"


def _settings_row(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        enabled=True,
        stripe_secret_key_encrypted="cifrado",
        stripe_webhook_secret_encrypted="cifrado-webhook",
        end_customer_tokens_per_credit=500,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _package(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        id=PACKAGE_ID, tenant_id=TENANT_ID, name="Básico", price_brl=Decimal("49.90"),
        credits_granted=500, active=True,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


@pytest.fixture
def session():
    return AsyncMock()


class TestCreateEndCustomerCheckoutSession:
    async def test_sem_settings_levanta_erro(self, session) -> None:
        session.scalar = AsyncMock(return_value=None)

        with pytest.raises(BillingNotConfiguredError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

    async def test_settings_desabilitado_levanta_erro(self, session) -> None:
        session.scalar = AsyncMock(return_value=_settings_row(enabled=False))

        with pytest.raises(BillingNotConfiguredError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

    async def test_enabled_sem_secret_key_levanta_erro(self, session) -> None:
        settings_sem_key = _settings_row(stripe_secret_key_encrypted=None)
        session.scalar = AsyncMock(return_value=settings_sem_key)

        with pytest.raises(BillingNotConfiguredError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

    async def test_pacote_inexistente_levanta_erro(self, session) -> None:
        session.scalar = AsyncMock(side_effect=[_settings_row(), None])

        with pytest.raises(InvalidPackageError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

    async def test_pacote_inativo_levanta_erro(self, session) -> None:
        session.scalar = AsyncMock(side_effect=[_settings_row(), _package(active=False)])

        with pytest.raises(InvalidPackageError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

    async def test_sucesso_usa_secret_key_do_tenant_e_metadata_correta(
        self, session, monkeypatch
    ) -> None:
        session.scalar = AsyncMock(side_effect=[_settings_row(), _package()])
        monkeypatch.setattr(service, "decrypt_tenant_secret", lambda v: "sk_test_do_tenant")
        created = MagicMock(
            return_value=SimpleNamespace(url="https://checkout.stripe.com/pay/cs_end_1")
        )
        monkeypatch.setattr(service.stripe.checkout.Session, "create", created)

        url = await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

        assert url == "https://checkout.stripe.com/pay/cs_end_1"
        kwargs = created.call_args.kwargs
        assert kwargs["api_key"] == "sk_test_do_tenant"
        assert kwargs["mode"] == "payment"
        assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 4990
        assert kwargs["metadata"] == {
            "tenant_id": str(TENANT_ID),
            "contact_phone_number": CONTACT,
            "package_id": str(PACKAGE_ID),
            "kind": "end_customer_purchase",
        }

    async def test_falha_na_stripe_levanta_stripe_api_error(self, session, monkeypatch) -> None:
        session.scalar = AsyncMock(side_effect=[_settings_row(), _package()])
        monkeypatch.setattr(service, "decrypt_tenant_secret", lambda v: "sk_test_do_tenant")

        def _raise(*args, **kwargs):
            raise service.stripe.error.StripeError("falhou")

        monkeypatch.setattr(service.stripe.checkout.Session, "create", _raise)

        with pytest.raises(StripeApiError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)
