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
    monkeypatch.setattr(
        stripe_connect_module, "_fetch_live_account_status", AsyncMock(return_value="onboarding")
    )

    client_secret = await create_or_refresh_connect_account(session, TENANT_ID)

    assert client_secret == "secret_abc"
    assert row.stripe_account_id == "acct_novo"
    assert row.stripe_account_status == "onboarding"
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_get_account_earnings_soma_saldo_e_repasses_em_brl(monkeypatch):
    from app.services.stripe_connect import get_account_earnings

    balance = {
        "available": [{"amount": 12345, "currency": "brl"}, {"amount": 500, "currency": "usd"}],
        "pending": [{"amount": 2000, "currency": "brl"}],
    }
    payouts = {
        "data": [
            {"amount": 10000, "status": "paid", "arrival_date": 0},
            {"amount": 500, "status": "pending", "arrival_date": None},
        ]
    }
    monkeypatch.setattr(
        stripe_connect_module.stripe.Balance, "retrieve", MagicMock(return_value=balance)
    )
    monkeypatch.setattr(
        stripe_connect_module.stripe.Payout, "list", MagicMock(return_value=payouts)
    )

    result = await get_account_earnings("acct_123")

    assert result.available_brl == 123.45
    assert result.pending_brl == 20.0
    assert len(result.recent_payouts) == 2
    assert result.recent_payouts[0].amount_brl == 100.0
    assert result.recent_payouts[0].status == "paid"
    assert result.recent_payouts[0].arrival_date == "1970-01-01"
    assert result.recent_payouts[1].arrival_date is None


@pytest.mark.asyncio
async def test_get_account_earnings_falha_na_stripe_levanta_connect_api_error(monkeypatch):
    import stripe

    from app.services.stripe_connect import get_account_earnings

    def _raise(*args, **kwargs):
        raise stripe.error.StripeError("indisponível")

    monkeypatch.setattr(stripe_connect_module.stripe.Balance, "retrieve", _raise)

    with pytest.raises(ConnectApiError):
        await get_account_earnings("acct_123")


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
    monkeypatch.setattr(
        stripe_connect_module, "_fetch_live_account_status", AsyncMock(return_value="active")
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
    monkeypatch.setattr(
        stripe_connect_module, "_fetch_live_account_status", AsyncMock(return_value="onboarding")
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


@pytest.mark.asyncio
async def test_erro_da_stripe_ao_criar_account_session_levanta_connect_api_error(
    session, monkeypatch
):
    import stripe

    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        billing_provider="connect",
        stripe_account_id="acct_existente",
        stripe_account_status="active",
    )
    session.scalar.return_value = row
    monkeypatch.setattr(
        stripe_connect_module, "_fetch_live_account_status", AsyncMock(return_value="active")
    )

    async def _raise(*args, **kwargs):
        raise stripe.error.StripeError("falhou")

    monkeypatch.setattr(stripe_connect_module, "_create_account_session", _raise)

    with pytest.raises(ConnectApiError):
        await create_or_refresh_connect_account(session, TENANT_ID)


@pytest.mark.asyncio
async def test_atualiza_status_quando_stripe_reporta_diferente_do_banco(session, monkeypatch):
    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        billing_provider="connect",
        stripe_account_id="acct_existente",
        stripe_account_status="onboarding",
    )
    session.scalar.return_value = row
    monkeypatch.setattr(
        stripe_connect_module,
        "_create_account_session",
        AsyncMock(return_value=SimpleNamespace(client_secret="secret_novo")),
    )
    monkeypatch.setattr(
        stripe_connect_module, "_fetch_live_account_status", AsyncMock(return_value="active")
    )

    await create_or_refresh_connect_account(session, TENANT_ID)

    assert row.stripe_account_status == "active"
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_falha_ao_revalidar_status_nao_impede_account_session(session, monkeypatch):
    import stripe

    row = SimpleNamespace(
        tenant_id=TENANT_ID,
        billing_provider="connect",
        stripe_account_id="acct_existente",
        stripe_account_status="onboarding",
    )
    session.scalar.return_value = row
    monkeypatch.setattr(
        stripe_connect_module,
        "_create_account_session",
        AsyncMock(return_value=SimpleNamespace(client_secret="secret_novo")),
    )

    async def _raise(*args, **kwargs):
        raise stripe.error.StripeError("falhou")

    monkeypatch.setattr(stripe_connect_module, "_fetch_live_account_status", _raise)

    client_secret = await create_or_refresh_connect_account(session, TENANT_ID)

    assert client_secret == "secret_novo"
    assert row.stripe_account_status == "onboarding"


@pytest.mark.asyncio
async def test_solicita_capability_pix_apos_criar_conta(session, monkeypatch):
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
    request_pix_mock = AsyncMock()
    monkeypatch.setattr(stripe_connect_module, "_request_pix_capability", request_pix_mock)
    monkeypatch.setattr(
        stripe_connect_module, "_fetch_live_account_status", AsyncMock(return_value="onboarding")
    )

    await create_or_refresh_connect_account(session, TENANT_ID)

    request_pix_mock.assert_awaited_once_with("acct_novo")


@pytest.mark.asyncio
async def test_falha_ao_solicitar_pix_nao_impede_criacao_da_conta(session, monkeypatch):
    import stripe

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

    async def _raise(*args, **kwargs):
        raise stripe.error.StripeError("falhou ao solicitar pix")

    monkeypatch.setattr(stripe_connect_module, "_request_pix_capability", _raise)
    monkeypatch.setattr(
        stripe_connect_module, "_fetch_live_account_status", AsyncMock(return_value="onboarding")
    )

    client_secret = await create_or_refresh_connect_account(session, TENANT_ID)

    assert client_secret == "secret_abc"
    assert row.stripe_account_id == "acct_novo"
    assert row.stripe_account_status == "onboarding"
    session.commit.assert_awaited()
