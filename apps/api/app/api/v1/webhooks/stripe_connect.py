"""Webhook único de Stripe Connect — escuta eventos de TODAS as contas
conectadas dos tenants (cobrança do cliente final, billing_provider=
"connect"). Diferente de webhooks/stripe_tenant.py (modelo standalone antigo,
1 endpoint + 1 secret por tenant): aqui é 1 endpoint + 1 secret pra
plataforma inteira, tenant resolvido via event["account"].

Nota sobre o evento de status (`_STATUS_EVENT_TYPE`): confirmado contra a doc
atual da Stripe (docs.stripe.com/connect/accounts-v2/migrate-integration,
seção "Webhook events") e contra o SDK `stripe-python` instalado que contas
v2 continuam emitindo o evento v1 (snapshot) `account.updated` — inclusive
quando a mudança é de capability — através do mecanismo legado de Connect
webhooks (escopo "Connected accounts"), que é o único mecanismo compatível
com `stripe.Webhook.construct_event` (usado aqui e em stripe_tenant.py).
Existe também um evento v2 "thin" nativo,
`v2.core.account[configuration.merchant].capability_status_updated`, mas ele
usa uma API de eventos totalmente diferente (Events v2: sem
`event["account"]`, sem `data.object` — só `data.updated_capability` +
`related_object.url` pra buscar a conta via uma chamada HTTP extra) e
`stripe.Webhook.construct_event` rejeita esse formato explicitamente
("You passed a thin event notification..."). Adotar o evento v2 nativo
exigiria endpoint/secret/verificação de assinatura próprios — fora do
escopo desta task.
"""

import logging

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_system_session
from app.models import TenantBillingSettings
from app.services.end_customer_billing import process_end_customer_checkout_completed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/stripe/connect", tags=["webhooks"])

# v1 (snapshot) event — ver docstring do módulo pra confirmação/fonte.
_STATUS_EVENT_TYPE = "account.updated"

_ASSINATURA_INVALIDA = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST, detail="Assinatura inválida"
)


async def _resolve_account_status(account_payload: dict) -> str:
    """Deriva "onboarding"/"active" a partir da capability card_payments no
    payload do evento account.updated (shape v1: `capabilities.card_payments`
    é uma string "active"/"inactive"/"pending", não um dict aninhado — ver
    docstring do módulo) — ativo é o que libera o billing gate determinístico
    a considerar esse tenant configurado.

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
    return "onboarding"


@router.post("")
async def receive_connect_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    session: AsyncSession = Depends(get_system_session),
) -> dict:
    raw_body = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            raw_body, stripe_signature, settings.stripe_connect_webhook_secret
        )
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        logger.warning("Assinatura de webhook Connect inválida | erro=%s", exc)
        raise _ASSINATURA_INVALIDA

    # event é um stripe.Event real (StripeObject): não implementa .get(),
    # só []/in — .to_dict() normaliza pra dict puro (mesmo padrão de
    # process_checkout_completed em app/services/billing.py).
    event_dict = event.to_dict() if hasattr(event, "to_dict") else dict(event)
    account_id = event_dict.get("account")
    if not account_id:
        return {"status": "ok"}

    billing_settings = await session.scalar(
        select(TenantBillingSettings).where(TenantBillingSettings.stripe_account_id == account_id)
    )
    if billing_settings is None:
        logger.warning("Evento Connect de conta desconhecida | account=%s", account_id)
        return {"status": "ok"}

    if event["type"] == "checkout.session.completed":
        await process_end_customer_checkout_completed(
            session, billing_settings.tenant_id, event["data"]["object"]
        )
    elif event["type"] == _STATUS_EVENT_TYPE:
        billing_settings.stripe_account_status = await _resolve_account_status(
            event["data"]["object"]
        )
        await session.commit()

    return {"status": "ok"}
