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
- A capability `pix_payments` não pode ser solicitada na chamada de criação
  da conta v2 (`configuration.merchant.capabilities` só aceita um
  subconjunto de capabilities — confirmado contra a doc de
  account-capabilities da Stripe, coluna "Suporte a Accounts v2" = "Não"
  pra `pix_payments`, nesta data). A API v1 clássica de Capabilities ainda
  consegue apontar pro `id` de uma conta v2 pra solicitar uma capability que
  a API v2 não expõe lá: `stripe.Account.modify_capability(account_id,
  "pix_payments", requested=True, api_key=...)` — classmethod top-level
  (`stripe.Account.modify_capability`), confirmado por introspecção
  (`inspect.signature`) e por uma chamada real com API key deliberadamente
  inválida (devolveu `AuthenticationError`, não `AttributeError`/
  `TypeError` — a forma da chamada é a certa, só a autenticação que falhou
  de propósito).
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import TenantBillingSettings
from app.schemas.end_customer_billing import ConnectEarningsOut, ConnectPayoutOut

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


async def _request_pix_capability(stripe_account_id: str) -> None:
    """Solicita a capability `pix_payments` numa chamada separada, via API v1
    de Capabilities, apontando pro `id` da conta v2 já criada.

    Por quê uma chamada separada: `pix_payments` não pode ser solicitada na
    chamada de criação da conta v2 — `configuration.merchant.capabilities`
    só aceita um subconjunto de capabilities (confirmado contra a doc de
    account-capabilities da Stripe, coluna "Suporte a Accounts v2" = "Não"
    pra `pix_payments`, nesta data). A API v1 clássica de Capabilities ainda
    consegue apontar pro `id` de uma conta v2 pra solicitar uma capability
    que a API v2 não expõe lá — é essa a chamada de fallback.
    """
    await asyncio.to_thread(
        stripe.Account.modify_capability,
        stripe_account_id,
        "pix_payments",
        requested=True,
        api_key=settings.stripe_connect_secret_key,
    )


async def resolve_account_status(account_payload) -> str:
    """Deriva "onboarding"/"in_review"/"active" a partir da capability
    card_payments e de details_submitted no payload de uma conta (shape v1:
    `capabilities.card_payments` é uma string "active"/"inactive"/"pending",
    não um dict aninhado). Usada tanto pelo webhook `account.updated`
    (`app/api/v1/webhooks/stripe_connect.py`, que importa esta função —
    mesma lógica, um único lugar) quanto pelo self-heal de
    `create_or_refresh_connect_account` abaixo.

    "in_review": o tenant já enviou os dados (details_submitted=true) mas a
    Stripe ainda não terminou de processar a revisão — capabilities.
    card_payments ainda não virou "active" (pode levar de segundos a
    minutos). Distinguir esse caso de "onboarding" (nunca enviou nada ainda)
    evita reapresentar o formulário de preenchimento — que faria o usuário
    achar que o envio anterior falhou — enquanto é só questão de esperar a
    Stripe terminar (ver EndCustomerBillingPanel.tsx).

    `account_payload` (e `capabilities` dentro dele) pode ser um StripeObject
    real (não um dict): não implementa `.get()`, só `[]`/`in` — `.to_dict()`
    normaliza pra dict puro antes de qualquer `.get()` (mesmo padrão de
    `process_checkout_completed` em `app/services/billing.py`)."""
    payload = (
        account_payload.to_dict() if hasattr(account_payload, "to_dict") else dict(account_payload)
    )
    capabilities = payload.get("capabilities", {})
    capabilities = (
        capabilities.to_dict() if hasattr(capabilities, "to_dict") else dict(capabilities)
    )
    if capabilities.get("card_payments") == "active":
        return "active"
    if payload.get("details_submitted"):
        return "in_review"
    return "onboarding"


async def _fetch_live_account_status(stripe_account_id: str) -> str:
    """Consulta o status real da conta na Stripe (v1 Account.retrieve,
    mesma chamada já usada por get_account_earnings/onboarding — a RAK
    connect já tem essa permissão confirmada) — não depende só do webhook
    `account.updated` pra manter `stripe_account_status` correto. Motivação:
    o webhook pode nunca chegar (endpoint mal configurado do lado da Stripe,
    evento não assinado, etc.) e o painel (`EndCustomerBillingPanel.tsx`)
    usa esse campo pra decidir se ainda mostra o onboarding embutido — sem
    esse self-heal, uma conta já totalmente ativa na Stripe ficaria presa
    mostrando o formulário de onboarding pra sempre."""
    account = await asyncio.to_thread(
        stripe.Account.retrieve, stripe_account_id, api_key=settings.stripe_connect_secret_key
    )
    return await resolve_account_status(account)


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

        # pix_payments não pode ser solicitada na criação da conta v2 (ver
        # docstring do módulo) — chamada de fallback via API v1 de
        # Capabilities, best-effort: uma falha aqui nunca deve impedir a
        # conta conectada de existir e seguir pro onboarding (card_payments
        # já foi concedida na criação); só loga e segue.
        try:
            await _request_pix_capability(account.id)
        except stripe.error.StripeError as exc:
            logger.warning(
                "Falha ao solicitar capability pix_payments (best-effort) | "
                "tenant=%s conta=%s erro=%s",
                tenant_id,
                account.id,
                exc,
            )

        await session.commit()

    try:
        live_status = await _fetch_live_account_status(row.stripe_account_id)
    except stripe.error.StripeError as exc:
        logger.warning(
            "Falha ao revalidar status da conta (best-effort, mantém o valor já salvo) | "
            "tenant=%s conta=%s erro=%s",
            tenant_id,
            row.stripe_account_id,
            exc,
        )
    else:
        if live_status != row.stripe_account_status:
            row.stripe_account_status = live_status
            await session.commit()

    try:
        account_session = await _create_account_session(row.stripe_account_id)
    except stripe.error.StripeError as exc:
        logger.error("Falha ao criar Account Session | tenant=%s erro=%s", tenant_id, exc)
        raise ConnectApiError("Falha ao iniciar a configuração de pagamentos") from exc

    return account_session.client_secret


def _sum_brl_cents(amounts: list) -> int:
    return sum(entry["amount"] for entry in amounts if entry["currency"] == "brl")


async def get_account_earnings(stripe_account_id: str) -> ConnectEarningsOut:
    """Saldo (disponível/pendente) e últimos repasses da conta conectada do
    tenant — direto da própria Stripe, nunca guardado em cache no nosso banco.

    Usa a mesma chave restrita do onboarding (settings.stripe_connect_secret_key),
    passando `stripe_account=` — é a mesma técnica de "operar em nome de uma
    conta conectada" que o checkout em Direct charge já usa, só que aqui é
    leitura (Balance/Payouts) em vez de escrita.
    """
    try:
        balance = await asyncio.to_thread(
            stripe.Balance.retrieve,
            api_key=settings.stripe_connect_secret_key,
            stripe_account=stripe_account_id,
        )
        payouts = await asyncio.to_thread(
            stripe.Payout.list,
            api_key=settings.stripe_connect_secret_key,
            stripe_account=stripe_account_id,
            limit=10,
        )
    except stripe.error.StripeError as exc:
        logger.error("Falha ao consultar saldo/repasses | conta=%s erro=%s", stripe_account_id, exc)
        raise ConnectApiError("Falha ao consultar o saldo — tente novamente em instantes") from exc

    return ConnectEarningsOut(
        available_brl=_sum_brl_cents(balance["available"]) / 100,
        pending_brl=_sum_brl_cents(balance["pending"]) / 100,
        recent_payouts=[
            ConnectPayoutOut(
                amount_brl=payout["amount"] / 100,
                status=payout["status"],
                arrival_date=(
                    datetime.fromtimestamp(payout["arrival_date"], tz=UTC).date().isoformat()
                    if payout["arrival_date"] is not None
                    else None
                ),
            )
            for payout in payouts["data"]
        ],
    )
