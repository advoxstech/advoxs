import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.stripe_connect as stripe_connect_module
from app.services.stripe_connect import ConnectApiError, create_or_refresh_connect_account

TENANT_ID = uuid.uuid4()


@pytest.fixture
def session():
    mock = AsyncMock()
    mock.add = MagicMock()
    return mock


@pytest.mark.asyncio
async def test_cria_conta_quando_tenant_nao_tem_stripe_account_id(session, monkeypatch):
    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        billing_provider="connect",
        stripe_account_id=None,
        stripe_account_status=None,
    )
    session.scalar.return_value = row
    created_account = SimpleNamespace(id="acct_novo")
    monkeypatch.setattr(
        stripe_connect_module,
        "_create_stripe_account",
        AsyncMock(return_value=created_account),
    )
    monkeypatch.setattr(
        stripe_connect_module,
        "_create_account_session",
        AsyncMock(return_value=SimpleNamespace(client_secret="secret_abc")),
    )

    client_secret = await create_or_refresh_connect_account(session, TENANT_ID)

    assert client_secret == "secret_abc"
    assert row.stripe_account_id == "acct_novo"
    assert row.stripe_account_status == "onboarding"
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_nao_recria_conta_quando_tenant_ja_tem_stripe_account_id(session, monkeypatch):
    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        billing_provider="connect",
        stripe_account_id="acct_existente",
        stripe_account_status="active",
    )
    session.scalar.return_value = row
    create_account_mock = AsyncMock()
    monkeypatch.setattr(stripe_connect_module, "_create_stripe_account", create_account_mock)
    monkeypatch.setattr(
        stripe_connect_module,
        "_create_account_session",
        AsyncMock(return_value=SimpleNamespace(client_secret="secret_novo")),
    )

    client_secret = await create_or_refresh_connect_account(session, TENANT_ID)

    assert client_secret == "secret_novo"
    create_account_mock.assert_not_awaited()
    assert row.stripe_account_id == "acct_existente"


@pytest.mark.asyncio
async def test_cria_linha_de_settings_quando_tenant_nunca_configurou_nada(session, monkeypatch):
    session.scalar.return_value = None
    added = []
    session.add = MagicMock(side_effect=lambda obj: added.append(obj))
    monkeypatch.setattr(
        stripe_connect_module,
        "_create_stripe_account",
        AsyncMock(return_value=SimpleNamespace(id="acct_novo")),
    )
    monkeypatch.setattr(
        stripe_connect_module,
        "_create_account_session",
        AsyncMock(return_value=SimpleNamespace(client_secret="secret_abc")),
    )

    await create_or_refresh_connect_account(session, TENANT_ID)

    assert len(added) == 1
    created_row = added[0]
    assert created_row.tenant_id == TENANT_ID
    assert created_row.billing_provider == "connect"


@pytest.mark.asyncio
async def test_erro_da_stripe_ao_criar_conta_levanta_connect_api_error(session, monkeypatch):
    import stripe

    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        billing_provider="connect",
        stripe_account_id=None,
        stripe_account_status=None,
    )
    session.scalar.return_value = row

    async def _raise(*args, **kwargs):
        raise stripe.error.StripeError("falhou")

    monkeypatch.setattr(stripe_connect_module, "_create_stripe_account", _raise)

    with pytest.raises(ConnectApiError):
        await create_or_refresh_connect_account(session, TENANT_ID)
