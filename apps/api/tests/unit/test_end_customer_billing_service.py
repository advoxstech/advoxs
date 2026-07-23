import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.end_customer_billing as service
from app.services.end_customer_billing import (
    BillingNotConfiguredError,
    EndCustomerBalanceNotFoundError,
    InvalidPackageError,
    StripeApiError,
    create_end_customer_checkout_session,
    list_customers,
    process_end_customer_checkout_completed,
    zero_end_customer_balance,
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


def _balance(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        contact_phone_number=CONTACT,
        credit_balance=Decimal("120.0000"),
        updated_at=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _package(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        id=PACKAGE_ID,
        tenant_id=TENANT_ID,
        name="Básico",
        price_brl=Decimal("49.90"),
        credits_granted=500,
        active=True,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture
def arq():
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
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        contact_phone_number=CONTACT,
        last_message_at=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _number(**overrides):
    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        phone_number_id="PNID",
        access_token_encrypted="cifrado",
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
    async def test_ja_processado_nao_faz_nada(self, session, arq) -> None:
        session.scalar = AsyncMock(return_value=uuid.uuid4())

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        session.add.assert_not_called()

    async def test_metadata_sem_kind_correto_e_ignorada(self, session, arq) -> None:
        session.scalar = AsyncMock(return_value=None)

        await process_end_customer_checkout_completed(
            session, TENANT_ID, _checkout_session(kind="outra_coisa"), arq
        )

        session.add.assert_not_called()

    async def test_metadata_sem_contact_phone_number_nao_processa(self, session, arq) -> None:
        session.scalar = AsyncMock(return_value=None)

        await process_end_customer_checkout_completed(
            session, TENANT_ID, _checkout_session(contact_phone_number=None), arq
        )

        session.add.assert_not_called()

    async def test_metadata_sem_package_id_nao_processa(self, session, arq) -> None:
        session.scalar = AsyncMock(return_value=None)

        await process_end_customer_checkout_completed(
            session, TENANT_ID, _checkout_session(package_id=None), arq
        )

        session.add.assert_not_called()

    async def test_pacote_nao_encontrado_nao_processa(self, session, arq) -> None:
        session.scalar = AsyncMock(side_effect=[None, None])

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        session.add.assert_not_called()

    async def test_credita_saldo_novo_e_manda_confirmacao(self, session, arq, monkeypatch) -> None:
        package = _package()
        conversation = _conversation()
        number = _number()
        session.scalar = AsyncMock(
            side_effect=[None, package, None, "block_with_message", conversation, number]
        )
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        session.flush = AsyncMock()
        send = AsyncMock()
        monkeypatch.setattr(service, "send_text_message", send)
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        balance, transaction, message, trigger_message = added
        assert balance.credit_balance == package.credits_granted
        assert transaction.type == "purchase"
        assert transaction.amount_credits == package.credits_granted
        assert transaction.stripe_payment_id == "cs_end_999"
        assert message.sender_type == "system"
        assert trigger_message.sender_type == "system"
        assert "pagamento" in trigger_message.content.lower()
        send.assert_awaited_once()
        assert send.await_args.kwargs["to"] == CONTACT
        session.commit.assert_awaited()

        # Aciona o agente pela mesma fila do webhook do WhatsApp, com o id da
        # mensagem de gatilho — é isso que faz a Sofia reagir sozinha, sem
        # depender do cliente digitar "já paguei".
        arq.enqueue_job.assert_awaited_once_with(
            "process_inbound_message",
            tenant_id=str(TENANT_ID),
            conversation_id=str(conversation.id),
            message_id=str(trigger_message.id),
        )

    async def test_credita_saldo_existente_soma(self, session, arq, monkeypatch) -> None:
        package = _package()
        existing_balance = SimpleNamespace(
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            credit_balance=100,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.scalar = AsyncMock(
            side_effect=[None, package, existing_balance, "block_with_message", None, None]
        )
        session.add = MagicMock()
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        assert existing_balance.credit_balance == 100 + package.credits_granted

    async def test_falha_ao_confirmar_via_whatsapp_nao_impede_credito(
        self, session, arq, monkeypatch
    ) -> None:
        package = _package()
        session.scalar = AsyncMock(
            side_effect=[None, package, None, "block_with_message", None, None]
        )
        session.add = MagicMock()
        monkeypatch.setattr(
            service, "send_text_message", AsyncMock(side_effect=RuntimeError("falhou"))
        )

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        # Falha antes de chegar na mensagem de gatilho — não deve acionar o agente.
        arq.enqueue_job.assert_not_called()

        session.commit.assert_awaited()

    async def test_transiciona_billing_gate_para_agent_quando_deterministic_gate(
        self, session, arq, monkeypatch
    ) -> None:
        package = _package()
        conversation = _conversation(
            state="billing_gate", billing_gate_step="aguardando_pagamento", billing_gate_retries=1
        )
        number = _number()
        session.scalar = AsyncMock(
            side_effect=[None, package, None, "deterministic_gate", conversation, number]
        )
        session.add = MagicMock()
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        assert conversation.state == "agent"
        assert conversation.billing_gate_step is None
        assert conversation.billing_gate_retries == 0
        arq.enqueue_job.assert_not_called()

    async def test_nao_transiciona_conversa_em_human_mesmo_com_deterministic_gate(
        self, session, arq, monkeypatch
    ) -> None:
        package = _package()
        conversation = _conversation(state="human")
        number = _number()
        session.scalar = AsyncMock(
            side_effect=[None, package, None, "deterministic_gate", conversation, number]
        )
        session.add = MagicMock()
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session(), arq)

        assert conversation.state == "human"


class TestListCustomers:
    async def test_agrega_saldo_compra_e_consumo_por_contato(self) -> None:
        session = AsyncMock()
        result = MagicMock()
        result.all.return_value = [
            ("5511999990001", 120.0, 500.0, -380.0),
        ]
        session.execute.return_value = result

        customers = await list_customers(session, TENANT_ID, 50, 0)

        assert len(customers) == 1
        assert customers[0].contact_phone_number == "5511999990001"
        assert customers[0].credit_balance == 120.0
        assert customers[0].total_purchased == 500.0
        assert customers[0].total_consumed == 380.0  # abs()

    async def test_sem_clientes_retorna_lista_vazia(self) -> None:
        session = AsyncMock()
        result = MagicMock()
        result.all.return_value = []
        session.execute.return_value = result

        customers = await list_customers(session, TENANT_ID, 50, 0)

        assert customers == []

    async def test_query_filtra_por_tenant_id(self) -> None:
        session = AsyncMock()
        result = MagicMock()
        result.all.return_value = []
        session.execute.return_value = result

        await list_customers(session, TENANT_ID, 50, 0)

        query = session.execute.call_args.args[0]
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
        assert "tenant_id" in compiled


class TestZeroEndCustomerBalance:
    async def test_zera_saldo_e_lanca_ajuste_no_ledger(self) -> None:
        session = AsyncMock()
        balance = _balance()
        session.scalar = AsyncMock(return_value=balance)
        session.add = MagicMock()

        await zero_end_customer_balance(session, TENANT_ID, CONTACT)

        assert balance.credit_balance == 0
        session.add.assert_called_once()
        transaction = session.add.call_args.args[0]
        assert transaction.type == "adjustment"
        assert transaction.amount_credits == Decimal("-120.0000")
        assert transaction.contact_phone_number == CONTACT
        session.commit.assert_awaited_once()

    async def test_saldo_ja_zerado_nao_faz_nada(self) -> None:
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=_balance(credit_balance=Decimal("0")))
        session.add = MagicMock()

        await zero_end_customer_balance(session, TENANT_ID, CONTACT)

        session.add.assert_not_called()
        session.commit.assert_not_awaited()

    async def test_contato_sem_saldo_levanta_erro(self) -> None:
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)

        with pytest.raises(EndCustomerBalanceNotFoundError):
            await zero_end_customer_balance(session, TENANT_ID, CONTACT)
