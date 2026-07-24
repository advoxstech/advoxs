"""Onboarding da conta conectada (Stripe Connect, Accounts v2) do tenant —
cobrança do cliente final. Substitui, para tenants em billing_provider=
"connect", o modelo antigo de colar secret key/webhook secret
(billing_provider="standalone", ver app/services/end_customer_billing.py).

Usa uma chave restrita própria (settings.stripe_connect_secret_key, escopo
Connected Accounts + Account Sessions) — nunca a mesma chave usada pelo
billing tenant->Advoxs (settings.stripe_secret_key, escopo só Checkout
Sessions) nem a secret key cifrada de nenhum tenant.

Forma de chamada confirmada contra stripe-python 15.3.0 (ver relatório da
task para o passo a passo da verificação):
- Accounts v2 não tem um classmethod top-level (`stripe.v2.core.Account.create`
  não existe nesta versão). É preciso um `stripe.StripeClient(api_key=...)`
  instanciado por chamada — `client.v2.core.accounts.create(params=...)` — e
  esse client concentra o `api_key`, já que o serviço v2 não aceita
  `api_key=` como kwarg direto. Ainda assim nunca usamos `stripe.api_key`
  global: cada chamada cria seu próprio `StripeClient` com a chave certa.
- Account Session é API v1 clássica (`stripe.AccountSession.create`), que
  aceita `api_key=` como kwarg por chamada — mesmo padrão já usado em
  `app/services/end_customer_billing.py`.
"""

import asyncio
import logging
import uuid

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import TenantBillingSettings

logger = logging.getLogger(__name__)


class ConnectApiError(Exception):
    """Falha ao criar/atualizar a conta conectada ou a Account Session."""


async def _create_stripe_account() -> "stripe.v2.core.Account":
    client = stripe.StripeClient(api_key=settings.stripe_connect_secret_key)
    return await asyncio.to_thread(
        client.v2.core.accounts.create,
        params={
            "identity": {"country": "BR"},
            "dashboard": "full",
            "configuration": {
                "merchant": {
                    "capabilities": {
                        "card_payments": {"requested": True},
                    }
                }
            },
            "defaults": {
                "responsibilities": {
                    "fees_collector": "stripe",
                    "losses_collector": "stripe",
                }
            },
        },
    )


async def _create_account_session(stripe_account_id: str) -> "stripe.AccountSession":
    return await asyncio.to_thread(
        stripe.AccountSession.create,
        api_key=settings.stripe_connect_secret_key,
        account=stripe_account_id,
        components={"account_onboarding": {"enabled": True}},
    )


async def create_or_refresh_connect_account(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    """Cria a conta v2 na primeira chamada; chamadas seguintes só geram uma
    nova Account Session pra conta já existente. Devolve o client_secret da
    Account Session, usado pelo frontend pra inicializar o Connect.js."""
    row = await session.scalar(
        select(TenantBillingSettings).where(TenantBillingSettings.tenant_id == tenant_id)
    )
    if row is None:
        row = TenantBillingSettings(
            tenant_id=tenant_id, enabled=False, billing_mode="credits", billing_provider="connect"
        )
        session.add(row)
        await session.flush()

    if row.stripe_account_id is None:
        try:
            account = await _create_stripe_account()
        except stripe.error.StripeError as exc:
            logger.error("Falha ao criar conta conectada | tenant=%s erro=%s", tenant_id, exc)
            raise ConnectApiError("Falha ao iniciar a configuração de pagamentos") from exc

        row.stripe_account_id = account.id
        row.stripe_account_status = "onboarding"
        row.billing_provider = "connect"
        await session.commit()

    try:
        account_session = await _create_account_session(row.stripe_account_id)
    except stripe.error.StripeError as exc:
        logger.error("Falha ao criar Account Session | tenant=%s erro=%s", tenant_id, exc)
        raise ConnectApiError("Falha ao iniciar a configuração de pagamentos") from exc

    return account_session.client_secret
