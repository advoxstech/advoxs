import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import stripe

import app.services.end_customer_billing as service
from app.schemas.end_customer_billing import RevenueByCustomerOut, RevenueByMonthOut
from app.services.end_customer_billing import (
    BillingNotConfiguredError,
    EndCustomerBalanceNotFoundError,
    InvalidPackageError,
    StripeApiError,
    create_end_customer_checkout_session,
    get_revenue_report,
    list_customers,
    process_end_customer_checkout_completed,
    process_end_customer_subscription_created,
    process_end_customer_subscription_renewed,
    process_end_customer_subscription_status_changed,
    zero_end_customer_balance,
)

TENANT_ID = uuid.uuid4()
PACKAGE_ID = uuid.uuid4()
CONTACT = "5511999998888"


def _settings_row(**overrides) -> SimpleNamespace:
    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        enabled=True,
        billing_provider="standalone",
        stripe_account_id=None,
        stripe_account_status=None,
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
        kind="one_time",
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

    async def test_checkout_connect_usa_direct_charge_na_conta_do_tenant(
        self, session, monkeypatch
    ) -> None:
        session.scalar = AsyncMock(
            side_effect=[
                _settings_row(
                    billing_provider="connect",
                    stripe_account_id="acct_123",
                    stripe_account_status="active",
                ),
                _package(),
            ]
        )
        created = MagicMock(
            return_value=SimpleNamespace(url="https://checkout.stripe.com/pay/cs_connect")
        )
        monkeypatch.setattr(service.stripe.checkout.Session, "create", created)

        url = await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

        assert url == "https://checkout.stripe.com/pay/cs_connect"
        kwargs = created.call_args.kwargs
        assert kwargs["stripe_account"] == "acct_123"
        assert kwargs["api_key"] == service.settings.stripe_connect_secret_key
        assert "application_fee_amount" not in kwargs

    async def test_checkout_connect_sem_stripe_account_id_levanta_erro(self, session) -> None:
        session.scalar = AsyncMock(
            return_value=_settings_row(billing_provider="connect", stripe_account_id=None)
        )

        with pytest.raises(BillingNotConfiguredError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

    async def test_checkout_connect_status_nao_active_levanta_erro(self, session) -> None:
        """Conta conectada existe (stripe_account_id setado) mas a capability
        ainda não está ativa (onboarding pendente, ou regrediu depois de já ter
        ficado ativa) — não basta ter um account_id, o status precisa ser
        "active" pra gerar um checkout que de fato aceita cobrança."""
        session.scalar = AsyncMock(
            return_value=_settings_row(
                billing_provider="connect",
                stripe_account_id="acct_123",
                stripe_account_status="onboarding",
            )
        )

        with pytest.raises(BillingNotConfiguredError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

    async def test_checkout_connect_status_none_levanta_erro(self, session) -> None:
        session.scalar = AsyncMock(
            return_value=_settings_row(
                billing_provider="connect",
                stripe_account_id="acct_123",
                stripe_account_status=None,
            )
        )

        with pytest.raises(BillingNotConfiguredError):
            await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

    async def test_checkout_de_assinatura_usa_mode_subscription_e_recurring(
        self, session, monkeypatch
    ) -> None:
        session.scalar = AsyncMock(
            side_effect=[
                _settings_row(
                    billing_provider="connect",
                    stripe_account_id="acct_123",
                    stripe_account_status="active",
                ),
                _package(kind="subscription", credits_granted=None),
            ]
        )
        created = MagicMock(
            return_value=SimpleNamespace(url="https://checkout.stripe.com/pay/cs_sub_1")
        )
        monkeypatch.setattr(service.stripe.checkout.Session, "create", created)

        url = await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

        assert url == "https://checkout.stripe.com/pay/cs_sub_1"
        kwargs = created.call_args.kwargs
        assert kwargs["mode"] == "subscription"
        assert kwargs["line_items"][0]["price_data"]["recurring"] == {"interval": "month"}
        assert kwargs["metadata"]["kind"] == "end_customer_subscription"
        assert "application_fee_amount" not in kwargs

    async def test_checkout_de_pacote_avulso_continua_mode_payment(
        self, session, monkeypatch
    ) -> None:
        session.scalar = AsyncMock(
            side_effect=[
                _settings_row(
                    billing_provider="connect",
                    stripe_account_id="acct_123",
                    stripe_account_status="active",
                ),
                _package(kind="one_time"),
            ]
        )
        created = MagicMock(
            return_value=SimpleNamespace(url="https://checkout.stripe.com/pay/cs_one_1")
        )
        monkeypatch.setattr(service.stripe.checkout.Session, "create", created)

        await create_end_customer_checkout_session(session, TENANT_ID, CONTACT, PACKAGE_ID)

        kwargs = created.call_args.kwargs
        assert kwargs["mode"] == "payment"
        assert "recurring" not in kwargs["line_items"][0]["price_data"]
        assert kwargs["metadata"]["kind"] == "end_customer_purchase"


def _conversation(**overrides):
    row = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        contact_phone_number=CONTACT,
        last_message_at=None,
        state="agent",
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

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        session.add.assert_not_called()

    async def test_metadata_sem_kind_correto_e_ignorada(self, session, arq) -> None:
        session.scalar = AsyncMock(return_value=None)

        await process_end_customer_checkout_completed(
            session, TENANT_ID, _checkout_session(kind="outra_coisa")
        )

        session.add.assert_not_called()

    async def test_metadata_sem_contact_phone_number_nao_processa(self, session, arq) -> None:
        session.scalar = AsyncMock(return_value=None)

        await process_end_customer_checkout_completed(
            session, TENANT_ID, _checkout_session(contact_phone_number=None)
        )

        session.add.assert_not_called()

    async def test_metadata_sem_package_id_nao_processa(self, session, arq) -> None:
        session.scalar = AsyncMock(return_value=None)

        await process_end_customer_checkout_completed(
            session, TENANT_ID, _checkout_session(package_id=None)
        )

        session.add.assert_not_called()

    async def test_pacote_nao_encontrado_nao_processa(self, session, arq) -> None:
        session.scalar = AsyncMock(side_effect=[None, None])

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        session.add.assert_not_called()

    async def test_credita_saldo_novo_e_manda_confirmacao(self, session, arq, monkeypatch) -> None:
        package = _package()
        conversation = _conversation()
        number = _number()
        session.scalar = AsyncMock(side_effect=[None, package, None, conversation, number])
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
        # Mecanismo antigo (mensagem de gatilho pro agents) foi removido —
        # nunca mais aciona nada por fila.
        arq.enqueue_job.assert_not_called()

    async def test_credita_saldo_existente_soma(self, session, arq, monkeypatch) -> None:
        package = _package()
        existing_balance = SimpleNamespace(
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            credit_balance=100,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.scalar = AsyncMock(side_effect=[None, package, existing_balance, None, None])
        session.add = MagicMock()
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        assert existing_balance.credit_balance == 100 + package.credits_granted

    async def test_falha_ao_confirmar_via_whatsapp_nao_impede_credito(
        self, session, arq, monkeypatch
    ) -> None:
        package = _package()
        session.scalar = AsyncMock(side_effect=[None, package, None, None, None])
        session.add = MagicMock()
        monkeypatch.setattr(
            service, "send_text_message", AsyncMock(side_effect=RuntimeError("falhou"))
        )

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        arq.enqueue_job.assert_not_called()
        session.commit.assert_awaited()

    async def test_transiciona_billing_gate_para_agent(self, session, arq, monkeypatch) -> None:
        package = _package()
        conversation = _conversation(
            state="billing_gate", billing_gate_step="aguardando_pagamento", billing_gate_retries=1
        )
        number = _number()
        session.scalar = AsyncMock(side_effect=[None, package, None, conversation, number])
        session.add = MagicMock()
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

        assert conversation.state == "agent"
        assert conversation.billing_gate_step is None
        assert conversation.billing_gate_retries == 0
        arq.enqueue_job.assert_not_called()

    async def test_nao_transiciona_conversa_em_human(self, session, arq, monkeypatch) -> None:
        package = _package()
        conversation = _conversation(state="human")
        number = _number()
        session.scalar = AsyncMock(side_effect=[None, package, None, conversation, number])
        session.add = MagicMock()
        monkeypatch.setattr(service, "send_text_message", AsyncMock())
        monkeypatch.setattr(service, "decrypt_access_token", lambda v: "token-claro")

        await process_end_customer_checkout_completed(session, TENANT_ID, _checkout_session())

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


class TestProcessEndCustomerSubscriptionCreated:
    async def test_cria_assinatura_e_notifica(self, session, monkeypatch) -> None:
        session.scalar = AsyncMock(return_value=None)
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        notify = AsyncMock()
        monkeypatch.setattr(service, "_notify_end_customer", notify)
        stripe_session = {
            "id": "cs_sub_1",
            "subscription": "sub_123",
            "metadata": {
                "kind": "end_customer_subscription",
                "contact_phone_number": CONTACT,
                "package_id": str(PACKAGE_ID),
            },
        }

        await process_end_customer_subscription_created(session, TENANT_ID, stripe_session)

        assert len(added) == 1
        created = added[0]
        assert created.tenant_id == TENANT_ID
        assert created.contact_phone_number == CONTACT
        assert created.stripe_subscription_id == "sub_123"
        assert created.status == "active"
        assert created.end_customer_credit_package_id == PACKAGE_ID
        notify.assert_awaited_once()
        assert notify.await_args.args[3] == "Assinatura ativada! Você já tem acesso ilimitado."
        assert notify.await_args.kwargs["exit_billing_gate"] is True

    async def test_duplicado_por_stripe_subscription_id_e_ignorado(
        self, session, monkeypatch
    ) -> None:
        session.scalar = AsyncMock(return_value=uuid.uuid4())
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        notify = AsyncMock()
        monkeypatch.setattr(service, "_notify_end_customer", notify)

        await process_end_customer_subscription_created(
            session, TENANT_ID, {"id": "cs_sub_1", "subscription": "sub_123", "metadata": {}}
        )

        assert added == []
        notify.assert_not_awaited()

    async def test_metadata_de_compra_avulsa_e_ignorada(self, session, monkeypatch) -> None:
        session.scalar = AsyncMock(return_value=None)
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        await process_end_customer_subscription_created(
            session,
            TENANT_ID,
            {
                "id": "cs_1",
                "subscription": None,
                "metadata": {"kind": "end_customer_purchase"},
            },
        )

        assert added == []

    async def test_reassinatura_atualiza_linha_existente_em_vez_de_inserir(
        self, session, monkeypatch
    ) -> None:
        """Cliente que cancelou e re-assinou: a linha antiga sobrevive (só o
        status muda no cancelamento), e a unique constraint é
        (tenant_id, contact_phone_number) — não stripe_subscription_id
        sozinho. Um INSERT cego colidiria com IntegrityError. O primeiro
        session.scalar (idempotência por stripe_subscription_id NOVO) não
        acha nada; o segundo (lookup por tenant+contato) acha a linha
        cancelada, que deve ser atualizada no lugar."""
        existing = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            end_customer_credit_package_id=uuid.uuid4(),
            stripe_subscription_id="sub_old_canceled",
            status="canceled",
            updated_at=None,
        )
        session.scalar = AsyncMock(side_effect=[None, existing])
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        notify = AsyncMock()
        monkeypatch.setattr(service, "_notify_end_customer", notify)
        stripe_session = {
            "id": "cs_sub_2",
            "subscription": "sub_new_123",
            "metadata": {
                "kind": "end_customer_subscription",
                "contact_phone_number": CONTACT,
                "package_id": str(PACKAGE_ID),
            },
        }

        await process_end_customer_subscription_created(session, TENANT_ID, stripe_session)

        assert added == []
        assert existing.stripe_subscription_id == "sub_new_123"
        assert existing.status == "active"
        assert existing.end_customer_credit_package_id == PACKAGE_ID
        notify.assert_awaited_once()

    async def test_reassinatura_reseta_current_period_end_da_assinatura_antiga(
        self, session, monkeypatch
    ) -> None:
        """Regressão de review: a linha reaproveitada no cancela->reassina
        pode carregar um current_period_end do período ANTIGO (já no
        passado, de antes do cancelamento). Se não for resetado, a query de
        entitlement do worker (status == active AND (current_period_end IS
        NULL OR current_period_end >= now())) nega serviço até a próxima
        invoice.payment_succeeded chegar — e se invoice.payment_succeeded da
        NOVA assinatura chegar ANTES deste checkout.session.completed (Stripe
        não garante ordem), process_end_customer_subscription_renewed ainda
        não acha a linha pelo novo stripe_subscription_id (só setado aqui) e
        desiste, deixando o valor velho parado pra sempre."""
        existing = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            end_customer_credit_package_id=uuid.uuid4(),
            stripe_subscription_id="sub_old_canceled",
            status="canceled",
            current_period_end=datetime(2020, 1, 1, tzinfo=UTC),
            updated_at=None,
        )
        session.scalar = AsyncMock(side_effect=[None, existing])
        session.add = MagicMock()
        notify = AsyncMock()
        monkeypatch.setattr(service, "_notify_end_customer", notify)
        stripe_session = {
            "id": "cs_sub_3",
            "subscription": "sub_new_456",
            "metadata": {
                "kind": "end_customer_subscription",
                "contact_phone_number": CONTACT,
                "package_id": str(PACKAGE_ID),
            },
        }

        await process_end_customer_subscription_created(session, TENANT_ID, stripe_session)

        assert existing.current_period_end is None


class TestProcessEndCustomerSubscriptionRenewed:
    async def test_atualiza_current_period_end_sem_notificar(self, session, monkeypatch) -> None:
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            stripe_subscription_id="sub_123",
            status="past_due",
            current_period_end=None,
        )
        session.scalar = AsyncMock(return_value=subscription)
        notify = AsyncMock()
        monkeypatch.setattr(service, "_notify_end_customer", notify)
        invoice = {
            "subscription": "sub_123",
            "lines": {"data": [{"period": {"end": 1735689600}}]},
        }

        await process_end_customer_subscription_renewed(session, TENANT_ID, invoice)

        assert subscription.status == "active"
        assert subscription.current_period_end == datetime.fromtimestamp(1735689600, UTC)
        session.commit.assert_awaited_once()
        notify.assert_not_awaited()

    async def test_assinatura_nao_encontrada_e_ignorado(self, session) -> None:
        session.scalar = AsyncMock(return_value=None)

        await process_end_customer_subscription_renewed(
            session, TENANT_ID, {"subscription": "sub_desconhecida", "lines": {"data": []}}
        )

        session.commit.assert_not_awaited()

    async def test_extrai_subscription_id_do_formato_moderno_parent(
        self, session, monkeypatch
    ) -> None:
        """A partir de uma migração de versão da API da Stripe, `Invoice`
        deixou de expor `subscription` na raiz — passou a ficar em
        `parent.subscription_details.subscription`. Sem esse fallback, todo
        invoice real (formato atual) faria a função retornar sem fazer nada,
        já que `invoice.get("subscription")` sempre daria `None`."""
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            stripe_subscription_id="sub_123",
            status="past_due",
            current_period_end=None,
        )
        session.scalar = AsyncMock(return_value=subscription)
        notify = AsyncMock()
        monkeypatch.setattr(service, "_notify_end_customer", notify)
        invoice = {
            "parent": {"subscription_details": {"subscription": "sub_123"}},
            "lines": {"data": [{"period": {"end": 1735689600}}]},
        }

        await process_end_customer_subscription_renewed(session, TENANT_ID, invoice)

        assert subscription.status == "active"
        assert subscription.current_period_end == datetime.fromtimestamp(1735689600, UTC)
        session.commit.assert_awaited_once()
        notify.assert_not_awaited()

    async def test_registra_pagamento_no_historico_de_faturamento(
        self, session, monkeypatch
    ) -> None:
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            stripe_subscription_id="sub_123",
            status="past_due",
            current_period_end=None,
            end_customer_credit_package_id=PACKAGE_ID,
        )
        package = SimpleNamespace(id=PACKAGE_ID, price_brl=Decimal("49.90"))
        session.scalar = AsyncMock(side_effect=[subscription, None])
        session.get = AsyncMock(return_value=package)
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        invoice = {
            "id": "in_999",
            "subscription": "sub_123",
            "lines": {"data": [{"period": {"end": 1735689600}}]},
            "status_transitions": {"paid_at": 1735689500},
        }

        await process_end_customer_subscription_renewed(session, TENANT_ID, invoice)

        assert len(added) == 1
        payment = added[0]
        assert payment.tenant_id == TENANT_ID
        assert payment.contact_phone_number == CONTACT
        assert payment.end_customer_subscription_id == subscription.id
        assert payment.amount_brl == Decimal("49.90")
        assert payment.stripe_invoice_id == "in_999"
        assert payment.paid_at == datetime.fromtimestamp(1735689500, UTC)

    async def test_sem_status_transitions_usa_fallback_do_momento_do_processamento(
        self, session, monkeypatch
    ) -> None:
        """`status_transitions` pode vir ausente do payload (não só
        `paid_at=None` dentro dele) — este é o campo que o brief pediu pra
        confirmar com cautela (2 bugs reais já encontrados neste arquivo
        sobre suposições erradas do shape do `Invoice`), então o fallback
        pro momento do processamento precisa de cobertura direta no caminho
        em que o pagamento É de fato persistido (session.add chamado), não
        só nos caminhos que retornam antes de chegar em `_extract_paid_at`."""
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            stripe_subscription_id="sub_123",
            status="past_due",
            current_period_end=None,
            end_customer_credit_package_id=PACKAGE_ID,
        )
        package = SimpleNamespace(id=PACKAGE_ID, price_brl=Decimal("49.90"))
        session.scalar = AsyncMock(side_effect=[subscription, None])
        session.get = AsyncMock(return_value=package)
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        invoice = {
            "id": "in_999",
            "subscription": "sub_123",
            "lines": {"data": [{"period": {"end": 1735689600}}]},
        }

        before = datetime.now(UTC)
        await process_end_customer_subscription_renewed(session, TENANT_ID, invoice)
        after = datetime.now(UTC)

        assert len(added) == 1
        payment = added[0]
        assert before <= payment.paid_at <= after

    async def test_invoice_duplicado_nao_registra_pagamento_2x(self, session, monkeypatch) -> None:
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            stripe_subscription_id="sub_123",
            status="active",
            current_period_end=None,
            end_customer_credit_package_id=PACKAGE_ID,
        )
        session.scalar = AsyncMock(side_effect=[subscription, uuid.uuid4()])
        session.add = MagicMock()
        invoice = {"id": "in_999", "subscription": "sub_123", "lines": {"data": []}}

        await process_end_customer_subscription_renewed(session, TENANT_ID, invoice)

        session.add.assert_not_called()

    async def test_assinatura_sem_pacote_nao_quebra_atualizacao_de_status(
        self, session, monkeypatch
    ) -> None:
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            stripe_subscription_id="sub_123",
            status="past_due",
            current_period_end=None,
            end_customer_credit_package_id=None,
        )
        session.scalar = AsyncMock(side_effect=[subscription, None])
        session.add = MagicMock()
        invoice = {"id": "in_999", "subscription": "sub_123", "lines": {"data": []}}

        await process_end_customer_subscription_renewed(session, TENANT_ID, invoice)

        assert subscription.status == "active"
        session.add.assert_not_called()
        session.commit.assert_awaited_once()


class TestProcessEndCustomerSubscriptionStatusChanged:
    async def test_cancelamento_notifica(self, session, monkeypatch) -> None:
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            stripe_subscription_id="sub_123",
            status="active",
        )
        session.scalar = AsyncMock(return_value=subscription)
        notify = AsyncMock()
        monkeypatch.setattr(service, "_notify_end_customer", notify)

        await process_end_customer_subscription_status_changed(
            session, TENANT_ID, {"id": "sub_123", "status": "canceled"}, notify_cancel=True
        )

        assert subscription.status == "canceled"
        session.commit.assert_awaited_once()
        notify.assert_awaited_once()
        assert notify.await_args.args[3] == (
            "Sua assinatura mensal foi cancelada — o atendimento volta a consumir "
            "créditos normalmente."
        )

    async def test_atualizacao_sem_cancelamento_nao_notifica(self, session, monkeypatch) -> None:
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            stripe_subscription_id="sub_123",
            status="active",
        )
        session.scalar = AsyncMock(return_value=subscription)
        notify = AsyncMock()
        monkeypatch.setattr(service, "_notify_end_customer", notify)

        await process_end_customer_subscription_status_changed(
            session, TENANT_ID, {"id": "sub_123", "status": "past_due"}, notify_cancel=False
        )

        assert subscription.status == "past_due"
        notify.assert_not_awaited()


class TestSubscriptionWebhooksComStripeObjectReal:
    """Regressão: `event["data"]["object"]` de um evento Stripe real (não
    mockado como dict puro) é um `StripeObject` de verdade — `stripe.Event`,
    o `.data.object` dele, é um `checkout.Session`/`Invoice`/`Subscription`
    real, sem `.get()` (só `[]`/`in`). Os testes acima mockam esses payloads
    como dict puro, o que mascara esse bug (dict tem `.get()`). Sem
    `_as_plain_dict` normalizando o payload logo no início de cada função,
    todo webhook real de assinatura quebraria com `AttributeError('get')`."""

    async def test_subscription_created_aceita_stripeobject_real(
        self, session, monkeypatch
    ) -> None:
        session.scalar = AsyncMock(return_value=None)
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))
        monkeypatch.setattr(service, "_notify_end_customer", AsyncMock())
        real_session = stripe.checkout.Session.construct_from(
            {
                "id": "cs_sub_real",
                "subscription": "sub_real_1",
                "metadata": {
                    "kind": "end_customer_subscription",
                    "contact_phone_number": CONTACT,
                    "package_id": str(PACKAGE_ID),
                },
            },
            "sk_test_fake",
        )

        await process_end_customer_subscription_created(session, TENANT_ID, real_session)

        assert len(added) == 1
        assert added[0].stripe_subscription_id == "sub_real_1"

    async def test_subscription_renewed_aceita_stripeobject_real(
        self, session, monkeypatch
    ) -> None:
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            stripe_subscription_id="sub_real_1",
            status="past_due",
            current_period_end=None,
        )
        session.scalar = AsyncMock(return_value=subscription)
        real_invoice = stripe.Invoice.construct_from(
            {
                "id": "in_real_1",
                "subscription": "sub_real_1",
                "lines": {
                    "object": "list",
                    "data": [{"id": "il_1", "period": {"start": 1, "end": 1735689600}}],
                },
            },
            "sk_test_fake",
        )

        await process_end_customer_subscription_renewed(session, TENANT_ID, real_invoice)

        assert subscription.status == "active"
        assert subscription.current_period_end == datetime.fromtimestamp(1735689600, UTC)

    async def test_subscription_status_changed_aceita_stripeobject_real(
        self, session, monkeypatch
    ) -> None:
        subscription = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            contact_phone_number=CONTACT,
            stripe_subscription_id="sub_real_1",
            status="active",
        )
        session.scalar = AsyncMock(return_value=subscription)
        notify = AsyncMock()
        monkeypatch.setattr(service, "_notify_end_customer", notify)
        real_subscription = stripe.Subscription.construct_from(
            {"id": "sub_real_1", "status": "canceled"}, "sk_test_fake"
        )

        await process_end_customer_subscription_status_changed(
            session, TENANT_ID, real_subscription, notify_cancel=True
        )

        assert subscription.status == "canceled"
        notify.assert_awaited_once()


class TestGetRevenueReport:
    async def test_soma_compra_avulsa_e_pagamento_de_assinatura_por_mes_e_cliente(
        self, session
    ) -> None:
        purchase_rows = [
            (datetime(2026, 7, 1, tzinfo=UTC), "5511999998888", Decimal("49.90")),
            (datetime(2026, 7, 15, tzinfo=UTC), "5511999997777", Decimal("99.90")),
        ]
        subscription_rows = [
            (datetime(2026, 7, 10, tzinfo=UTC), "5511999998888", Decimal("29.90")),
        ]
        purchase_result = MagicMock()
        purchase_result.all.return_value = purchase_rows
        subscription_result = MagicMock()
        subscription_result.all.return_value = subscription_rows
        session.execute = AsyncMock(side_effect=[purchase_result, subscription_result])

        report = await get_revenue_report(session, TENANT_ID, date(2026, 7, 1), date(2026, 7, 31))

        assert report.by_month == [RevenueByMonthOut(month="2026-07", total_brl=179.70)]
        assert report.by_customer[0] == RevenueByCustomerOut(
            contact_phone_number="5511999997777", total_brl=99.90
        )
        assert report.by_customer[1] == RevenueByCustomerOut(
            contact_phone_number="5511999998888", total_brl=79.80
        )

    async def test_sem_movimento_no_periodo_retorna_listas_vazias(self, session) -> None:
        empty_result = MagicMock()
        empty_result.all.return_value = []
        session.execute = AsyncMock(side_effect=[empty_result, empty_result])

        report = await get_revenue_report(session, TENANT_ID, date(2026, 7, 1), date(2026, 7, 31))

        assert report.by_month == []
        assert report.by_customer == []
