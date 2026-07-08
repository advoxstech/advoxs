import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.billing as billing
from app.services.billing import (
    EmailAlreadyExistsError,
    InvalidPackageError,
    StripeApiError,
    create_checkout_session,
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

    async def test_ja_processado_nao_faz_nada(self, session) -> None:
        session.scalar.return_value = uuid.uuid4()

        await process_checkout_completed(session, self._stripe_session())

        session.add.assert_not_called()

    async def test_cria_tenant_user_e_transacao(self, session) -> None:
        session.scalar.return_value = None
        session.get.return_value = _package()
        added = []
        session.add = MagicMock(side_effect=lambda obj: added.append(obj))

        async def fake_flush():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

        session.flush = AsyncMock(side_effect=fake_flush)

        await process_checkout_completed(session, self._stripe_session())

        assert len(added) == 3
        tenant, user, transaction = added
        assert tenant.name == "Escritório Teste"
        assert tenant.credit_balance == 2750
        assert user.email == "a@b.com"
        assert user.password_hash == "hash-fake"
        assert user.role == "admin"
        assert user.tenant_id == tenant.id
        assert transaction.amount_credits == 2750
        assert transaction.stripe_payment_id == "cs_123"
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
        session.scalar.return_value = None
        session.get.return_value = _package()
        session.add = MagicMock()

        async def fake_flush():
            pass

        session.flush = AsyncMock(side_effect=fake_flush)
        session.commit = AsyncMock(side_effect=billing.IntegrityError("stmt", {}, Exception("dup")))
        session.rollback = AsyncMock()

        await process_checkout_completed(session, self._stripe_session())

        session.rollback.assert_awaited_once()
