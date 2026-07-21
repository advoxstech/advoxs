import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import stripe

import app.services.billing as billing
from app.services.billing import (
    EmailAlreadyExistsError,
    InvalidPackageError,
    StripeApiError,
    create_checkout_session,
    create_recompra_checkout_session,
    process_checkout_completed,
)

PACKAGE_ID = uuid.uuid4()


def _package(active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=PACKAGE_ID,
        name="Growth",
        price_brl=Decimal("250.00"),
        credits_granted=2750,
        active=active,
    )


@pytest.fixture
def session():
    return AsyncMock()


class TestCreateCheckoutSession:
    async def test_email_ja_cadastrado_levanta_erro(self, session) -> None:
        session.scalar.return_value = uuid.uuid4()

        with pytest.raises(EmailAlreadyExistsError):
            await create_checkout_session(session, "Escritório", "a@b.com", "senha1234", PACKAGE_ID)

    async def test_pacote_inexistente_levanta_erro(self, session) -> None:
        session.scalar.return_value = None
        session.get.return_value = None

        with pytest.raises(InvalidPackageError):
            await create_checkout_session(session, "Escritório", "a@b.com", "senha1234", PACKAGE_ID)

    async def test_pacote_inativo_levanta_erro(self, session) -> None:
        session.scalar.return_value = None
        session.get.return_value = _package(active=False)

        with pytest.raises(InvalidPackageError):
            await create_checkout_session(session, "Escritório", "a@b.com", "senha1234", PACKAGE_ID)

    async def test_sucesso_cria_sessao_com_metadata_correta(self, session, monkeypatch) -> None:
        session.scalar.return_value = None
        session.get.return_value = _package()

        created = MagicMock(
            return_value=SimpleNamespace(url="https://checkout.stripe.com/pay/cs_123")
        )
        monkeypatch.setattr(billing.stripe.checkout.Session, "create", created)

        url = await create_checkout_session(
            session, "Escritório Teste", "a@b.com", "senha1234", PACKAGE_ID
        )

        assert url == "https://checkout.stripe.com/pay/cs_123"
        kwargs = created.call_args.kwargs
        assert kwargs["mode"] == "payment"
        assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 25000
        metadata = kwargs["metadata"]
        assert metadata["tenant_name"] == "Escritório Teste"
        assert metadata["email"] == "a@b.com"
        assert metadata["credit_package_id"] == str(PACKAGE_ID)
        assert "password_hash" in metadata
        assert metadata["password_hash"] != "senha1234"

    async def test_falha_na_stripe_levanta_stripe_api_error(self, session, monkeypatch) -> None:
        session.scalar.return_value = None
        session.get.return_value = _package()

        def _raise(*args, **kwargs):
            raise billing.stripe.error.StripeError("falhou")

        monkeypatch.setattr(billing.stripe.checkout.Session, "create", _raise)

        with pytest.raises(StripeApiError):
            await create_checkout_session(session, "Escritório", "a@b.com", "senha1234", PACKAGE_ID)


class TestCreateRecompraCheckoutSession:
    async def test_pacote_inexistente_levanta_erro(self, session) -> None:
        session.get.return_value = None

        with pytest.raises(InvalidPackageError):
            await create_recompra_checkout_session(session, uuid.uuid4(), PACKAGE_ID)

    async def test_pacote_inativo_levanta_erro(self, session) -> None:
        session.get.return_value = _package(active=False)

        with pytest.raises(InvalidPackageError):
            await create_recompra_checkout_session(session, uuid.uuid4(), PACKAGE_ID)

    async def test_sucesso_cria_sessao_com_metadata_de_recompra(self, session, monkeypatch) -> None:
        session.get.return_value = _package()
        created = MagicMock(
            return_value=SimpleNamespace(url="https://checkout.stripe.com/pay/cs_456")
        )
        monkeypatch.setattr(billing.stripe.checkout.Session, "create", created)
        tenant_id = uuid.uuid4()

        url = await create_recompra_checkout_session(session, tenant_id, PACKAGE_ID)

        assert url == "https://checkout.stripe.com/pay/cs_456"
        kwargs = created.call_args.kwargs
        assert kwargs["mode"] == "payment"
        assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 25000
        assert kwargs["metadata"] == {
            "flow": "recompra",
            "tenant_id": str(tenant_id),
            "credit_package_id": str(PACKAGE_ID),
        }
        assert "/creditos" in kwargs["success_url"]
        assert "/creditos" in kwargs["cancel_url"]

    async def test_falha_na_stripe_levanta_stripe_api_error(self, session, monkeypatch) -> None:
        session.get.return_value = _package()

        def _raise(*args, **kwargs):
            raise billing.stripe.error.StripeError("falhou")

        monkeypatch.setattr(billing.stripe.checkout.Session, "create", _raise)

        with pytest.raises(StripeApiError):
            await create_recompra_checkout_session(session, uuid.uuid4(), PACKAGE_ID)


class TestProcessCheckoutCompleted:
    def _stripe_session(self, **metadata_overrides) -> dict:
        metadata = {
            "tenant_name": "Escritório Teste",
            "email": "a@b.com",
            "password_hash": "hash-fake",
            "credit_package_id": str(PACKAGE_ID),
        }
        metadata.update(metadata_overrides)
        return {"id": "cs_123", "metadata": metadata}

    def _real_stripe_session(self, **metadata_overrides) -> "stripe.StripeObject":
        """Constrói um StripeObject real (não um dict) — reproduz o formato
        que `event['data']['object']` tem de verdade no webhook, onde `.get()`
        não existe (só `[]`/`in`); pego pelo bug real corrigido nesta task."""
        metadata = self._stripe_session(**metadata_overrides)["metadata"]
        return stripe.StripeObject.construct_from(
            {"id": "cs_123", "metadata": metadata}, "sk_test_fake"
        )

    async def test_ja_processado_nao_faz_nada(self, session) -> None:
        session.scalar.return_value = uuid.uuid4()

        await process_checkout_completed(session, self._stripe_session())

        session.add.assert_not_called()

    async def test_cria_tenant_user_e_transacao(self, session) -> None:
        session.scalar.side_effect = [None, SimpleNamespace(id=uuid.uuid4(), is_legacy=True)]
        session.get.return_value = _package()
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

        session.flush = AsyncMock(side_effect=fake_flush)

        await process_checkout_completed(session, self._stripe_session())

        # tenant + user + transaction + os 4 agentes padrão + a assinatura
        # padrão (plano Legado) — ver default_subscription.py.
        assert len(added) == 8
        tenant, user, transaction = added[:3]
        assert tenant.name == "Escritório Teste"
        assert tenant.credit_balance == 2750
        assert user.email == "a@b.com"
        assert user.password_hash == "hash-fake"
        assert user.role == "admin"
        assert user.tenant_id == tenant.id
        assert transaction.amount_credits == 2750
        assert transaction.stripe_payment_id == "cs_123"
        session.commit.assert_awaited_once()

    async def test_cria_4_agentes_padrao_para_o_tenant_novo(self, session) -> None:
        """C2: signup precisa provisionar os 4 agentes padrão (Secretária
        ponto de entrada + 3 especialistas) na MESMA transação — senão o
        tenant novo nasce sem nenhum agente (upload de KB/fallback quebram)."""
        session.scalar.side_effect = [None, SimpleNamespace(id=uuid.uuid4(), is_legacy=True)]
        session.get.return_value = _package()
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

        session.flush = AsyncMock(side_effect=fake_flush)

        await process_checkout_completed(session, self._stripe_session())

        tenant = added[0]
        agents_added = [obj for obj in added if type(obj).__name__ == "Agent"]
        assert len(agents_added) == 4
        assert all(a.tenant_id == tenant.id for a in agents_added)
        entry_points = [a for a in agents_added if a.is_entry_point]
        assert len(entry_points) == 1
        assert entry_points[0].name == "Secretária"
        names = {a.name for a in agents_added}
        assert names == {"Secretária", "Condominial", "Contratos", "Direito do Consumidor"}
        # Tudo na mesma transação: um único commit pro conjunto todo.
        session.commit.assert_awaited_once()

    async def test_cria_tenant_com_stripe_session_real_nao_dict(self, session) -> None:
        """Regressão: stripe_session é um StripeObject de verdade no webhook
        (não um dict de teste) — .get() não existe nele nem em .metadata,
        só []/in. Sem isso, o webhook real quebra com AttributeError('get')
        mesmo com os testes com dict passando."""
        session.scalar.side_effect = [None, SimpleNamespace(id=uuid.uuid4(), is_legacy=True)]
        session.get.return_value = _package()
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

        session.flush = AsyncMock(side_effect=fake_flush)

        await process_checkout_completed(session, self._real_stripe_session())

        assert len(added) == 8
        tenant, _user, _transaction = added[:3]
        assert tenant.name == "Escritório Teste"
        session.commit.assert_awaited_once()

    async def test_metadata_incompleta_nao_processa(self, session) -> None:
        session.scalar.return_value = None

        await process_checkout_completed(session, {"id": "cs_123", "metadata": {}})

        session.add.assert_not_called()

    async def test_pacote_nao_encontrado_nao_processa(self, session) -> None:
        session.scalar.return_value = None
        session.get.return_value = None

        await process_checkout_completed(session, self._stripe_session())

        session.add.assert_not_called()

    async def test_credit_package_id_malformado_nao_processa(self, session) -> None:
        session.scalar.return_value = None

        await process_checkout_completed(
            session, self._stripe_session(credit_package_id="not-a-uuid")
        )

        session.add.assert_not_called()

    async def test_integrity_error_no_commit_e_tratado(self, session) -> None:
        session.scalar.side_effect = [None, SimpleNamespace(id=uuid.uuid4(), is_legacy=True)]
        session.get.return_value = _package()
        session.add = MagicMock()

        async def fake_flush():
            pass

        session.flush = AsyncMock(side_effect=fake_flush)
        session.commit = AsyncMock(side_effect=billing.IntegrityError("stmt", {}, Exception("dup")))
        session.rollback = AsyncMock()

        await process_checkout_completed(session, self._stripe_session())

        session.rollback.assert_awaited_once()

    async def test_signup_gera_token_de_auto_login(self, session, monkeypatch) -> None:
        session.scalar.side_effect = [None, SimpleNamespace(id=uuid.uuid4(), is_legacy=True)]
        session.get.return_value = _package()
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

        session.flush = AsyncMock(side_effect=fake_flush)

        redis = AsyncMock()
        monkeypatch.setattr(billing, "get_redis", AsyncMock(return_value=redis))
        store_mock = AsyncMock()
        monkeypatch.setattr(billing, "store_login_token", store_mock)

        stripe_session = self._stripe_session()
        await process_checkout_completed(session, stripe_session)

        store_mock.assert_awaited_once()
        assert store_mock.await_args.args[1] == stripe_session["id"]
        _tenant, user, _transaction = added[:3]
        assert store_mock.await_args.args[2] == user.id

    async def test_falha_no_redis_nao_quebra_o_webhook(self, session, monkeypatch) -> None:
        session.scalar.side_effect = [None, SimpleNamespace(id=uuid.uuid4(), is_legacy=True)]
        session.get.return_value = _package()
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

        session.flush = AsyncMock(side_effect=fake_flush)

        monkeypatch.setattr(billing, "get_redis", AsyncMock(side_effect=RuntimeError("redis fora")))

        await process_checkout_completed(session, self._stripe_session())  # não levanta

        session.commit.assert_awaited_once()

    async def test_recompra_nao_gera_token(self, session, monkeypatch) -> None:
        session.scalar.return_value = None
        tenant = SimpleNamespace(id=uuid.uuid4(), credit_balance=500)
        session.get = AsyncMock(side_effect=[_package(), tenant])
        session.add = MagicMock()

        store_mock = AsyncMock()
        monkeypatch.setattr(billing, "store_login_token", store_mock)

        metadata = {
            "flow": "recompra",
            "tenant_id": str(tenant.id),
            "credit_package_id": str(PACKAGE_ID),
        }
        await process_checkout_completed(session, {"id": "cs_789", "metadata": metadata})

        store_mock.assert_not_awaited()

    async def test_cria_assinatura_legado_para_o_tenant_novo(self, session) -> None:
        """Até a Etapa 2 (Stripe/planos) substituir por escolha real de plano
        no cadastro, todo tenant novo recebe uma tenant_subscriptions
        apontando pro plano Legado — sem isso, POST /api/v1/agents e
        /knowledge-base/files quebrariam (RuntimeError de
        get_active_subscription) pra todo tenant criado nessa janela."""
        legado_plan = SimpleNamespace(id=uuid.uuid4(), is_legacy=True)
        session.scalar.side_effect = [None, legado_plan]
        session.get.return_value = _package()
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

        session.flush = AsyncMock(side_effect=fake_flush)

        await process_checkout_completed(session, self._stripe_session())

        tenant = added[0]
        subscriptions_added = [obj for obj in added if type(obj).__name__ == "TenantSubscription"]
        assert len(subscriptions_added) == 1
        assert subscriptions_added[0].tenant_id == tenant.id
        assert subscriptions_added[0].plan_id == legado_plan.id


class TestProcessCheckoutCompletedRecompra:
    def _recompra_session(self, **overrides) -> dict:
        metadata = {
            "flow": "recompra",
            "tenant_id": str(uuid.uuid4()),
            "credit_package_id": str(PACKAGE_ID),
        }
        metadata.update(overrides)
        return {"id": "cs_789", "metadata": metadata}

    async def test_credita_tenant_existente_sem_criar_user(self, session) -> None:
        session.scalar.return_value = None
        tenant = SimpleNamespace(id=uuid.uuid4(), credit_balance=500)
        session.get = AsyncMock(side_effect=[_package(), tenant])
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        await process_checkout_completed(session, self._recompra_session(tenant_id=str(tenant.id)))

        assert tenant.credit_balance == 500 + 2750
        assert len(added) == 1
        transaction = added[0]
        assert transaction.tenant_id == tenant.id
        assert transaction.type == "purchase"
        assert transaction.amount_credits == 2750
        assert transaction.stripe_payment_id == "cs_789"
        session.commit.assert_awaited_once()

    async def test_stripe_session_real_funciona_na_recompra(self, session) -> None:
        """Regressão: a mesma pegadinha do StripeObject sem .get() se aplica
        à recompra — cobrir explicitamente pra não regredir."""
        session.scalar.return_value = None
        tenant = SimpleNamespace(id=uuid.uuid4(), credit_balance=0)
        session.get = AsyncMock(side_effect=[_package(), tenant])
        session.add = MagicMock()

        raw = self._recompra_session(tenant_id=str(tenant.id))
        real_session = stripe.StripeObject.construct_from(raw, "sk_test_fake")

        await process_checkout_completed(session, real_session)

        assert tenant.credit_balance == 2750
        session.commit.assert_awaited_once()

    async def test_tenant_inexistente_nao_processa(self, session) -> None:
        session.scalar.return_value = None
        session.get = AsyncMock(side_effect=[_package(), None])
        session.add = MagicMock()

        await process_checkout_completed(session, self._recompra_session())

        session.add.assert_not_called()

    async def test_pacote_inexistente_na_recompra_nao_processa(self, session) -> None:
        session.scalar.return_value = None
        session.get = AsyncMock(return_value=None)
        session.add = MagicMock()

        await process_checkout_completed(session, self._recompra_session())

        session.add.assert_not_called()

    async def test_metadata_incompleta_na_recompra_nao_processa(self, session) -> None:
        session.scalar.return_value = None
        session.add = MagicMock()

        await process_checkout_completed(
            session, {"id": "cs_789", "metadata": {"flow": "recompra"}}
        )

        session.add.assert_not_called()

    async def test_integrity_error_na_recompra_e_tratado(self, session) -> None:
        session.scalar.return_value = None
        tenant = SimpleNamespace(id=uuid.uuid4(), credit_balance=0)
        session.get = AsyncMock(side_effect=[_package(), tenant])
        session.add = MagicMock()
        session.commit = AsyncMock(side_effect=billing.IntegrityError("stmt", {}, Exception("dup")))
        session.rollback = AsyncMock()

        await process_checkout_completed(session, self._recompra_session())

        session.rollback.assert_awaited_once()

    async def test_signup_sem_flow_continua_funcionando(self, session) -> None:
        """Regressão: metadata sem 'flow' (formato antigo, já em produção)
        continua indo pro fluxo de cadastro — nenhuma mudança observável."""
        session.scalar.side_effect = [None, SimpleNamespace(id=uuid.uuid4(), is_legacy=True)]
        session.get.return_value = _package()
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

        session.flush = AsyncMock(side_effect=fake_flush)

        metadata = {
            "tenant_name": "Escritório Teste",
            "email": "a@b.com",
            "password_hash": "hash-fake",
            "credit_package_id": str(PACKAGE_ID),
        }
        await process_checkout_completed(session, {"id": "cs_999", "metadata": metadata})

        assert len(added) == 8
        session.commit.assert_awaited_once()
