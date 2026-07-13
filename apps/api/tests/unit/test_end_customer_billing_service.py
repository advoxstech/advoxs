import uuid
from datetime import UTC, datetime
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
    process_end_customer_checkout_completed,
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


def _conversation(**overrides):
    row = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=TENANT_ID, contact_phone_number=CONTACT,
        last_message_at=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _number(**overrides):
    row = SimpleNamespace(
        tenant_id=TENANT_ID, phone_number_id="PNID", access_token_encrypted="cifrado",
        status="connected",
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _checkout_session(**metadata_overrides) -> dict:
    metadata = {
        "tenant_id": str(TENANT_ID),
        "contact_phone_number": CONTACT,
        "package_id": str(PACKAGE_ID),
        "kind": "end_customer_purchase",
    }
    metadata.update(metadata_overrides)
    return {"id": "cs_end_999", "metadata": metadata}


class TestProcessEndCustomerCheckoutCompleted:
    async def test_ja_processado_nao_faz_nada(self, session) -> None:
        session.scalar = AsyncMock(return_value=uuid.uuid4())

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        session.add.assert_not_called()

    async def test_metadata_sem_kind_correto_e_ignorada(self, session) -> None:
        session.scalar = AsyncMock(return_value=None)

        await process_end_customer_checkout_completed(
            session, TENANT_ID, _checkout_session(kind="outra_coisa")
        )

        session.add.assert_not_called()

    async def test_metadata_sem_contact_phone_number_nao_processa(self, session) -> None:
        session.scalar = AsyncMock(return_value=None)

        await process_end_customer_checkout_completed(
            session, TENANT_ID, _checkout_session(contact_phone_number=None)
        )

        session.add.assert_not_called()

    async def test_metadata_sem_package_id_nao_processa(self, session) -> None:
        session.scalar = AsyncMock(return_value=None)

        await process_end_customer_checkout_completed(
            session, TENANT_ID, _checkout_session(package_id=None)
        )

        session.add.assert_not_called()

    async def test_pacote_nao_encontrado_nao_processa(self, session) -> None:
        session.scalar = AsyncMock(side_effect=[None, None])

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        session.add.assert_not_called()

    async def test_credita_saldo_novo_e_manda_confirmacao(self, session, monkeypatch) -> None:
        package = _package()
        conversation = _conversation()
        number = _number()
        session.scalar = AsyncMock(
            side_effect=[None, package, None, conversation, number]
        )
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        session.flush = AsyncMock()
        send = AsyncMock()
        monkeypatch.setattr(service, "send_text_message", send)
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        balance, transaction, message = added
        assert balance.credit_balance == package.credits_granted
        assert transaction.type == "purchase"
        assert transaction.amount_credits == package.credits_granted
        assert transaction.stripe_payment_id == "cs_end_999"
        assert message.sender_type == "system"
        send.assert_awaited_once()
        assert send.await_args.kwargs["to"] == CONTACT
        session.commit.assert_awaited()

    async def test_credita_saldo_existente_soma(self, session, monkeypatch) -> None:
        package = _package()
        existing_balance = SimpleNamespace(
            tenant_id=TENANT_ID, contact_phone_number=CONTACT, credit_balance=100,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.scalar = AsyncMock(side_effect=[None, package, existing_balance, None, None])
        session.add = MagicMock()
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        assert existing_balance.credit_balance == 100 + package.credits_granted

    async def test_falha_ao_confirmar_via_whatsapp_nao_impede_credito(
        self, session, monkeypatch
    ) -> None:
        package = _package()
        session.scalar = AsyncMock(side_effect=[None, package, None, None, None])
        session.add = MagicMock()
        monkeypatch.setattr(
            service, "send_text_message", AsyncMock(side_effect=RuntimeError("falhou"))
        )

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        session.commit.assert_awaited()
