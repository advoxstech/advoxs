"""Configuração da cobrança do cliente final (Stripe própria do tenant) e
pacotes de crédito que o tenant vende aos próprios clientes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.core.crypto import encrypt_tenant_secret
from app.models import TenantBillingSettings
from app.schemas.end_customer_billing import TenantBillingSettingsOut, TenantBillingSettingsUpdate

router = APIRouter(prefix="/end-customer-billing", tags=["end-customer-billing"])


def _to_settings_out(settings_row: TenantBillingSettings | None) -> TenantBillingSettingsOut:
    if settings_row is None:
        return TenantBillingSettingsOut(
            enabled=False,
            billing_mode="credits",
            stripe_secret_key_configured=False,
            stripe_webhook_secret_configured=False,
            end_customer_tokens_per_credit=None,
        )
    return TenantBillingSettingsOut(
        enabled=settings_row.enabled,
        billing_mode=settings_row.billing_mode,
        stripe_secret_key_configured=settings_row.stripe_secret_key_encrypted is not None,
        stripe_webhook_secret_configured=settings_row.stripe_webhook_secret_encrypted is not None,
        end_customer_tokens_per_credit=settings_row.end_customer_tokens_per_credit,
    )


async def _get_settings_row(
    ctx: TenantContext, session: AsyncSession
) -> TenantBillingSettings | None:
    return await session.scalar(
        select(TenantBillingSettings).where(TenantBillingSettings.tenant_id == ctx.tenant_id)
    )


@router.get("/settings")
async def get_settings(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> TenantBillingSettingsOut:
    return _to_settings_out(await _get_settings_row(ctx, session))


@router.patch("/settings")
async def update_settings(
    body: TenantBillingSettingsUpdate,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> TenantBillingSettingsOut:
    row = await _get_settings_row(ctx, session)
    if row is None:
        # Valores explícitos (em vez de confiar no `server_default` das colunas):
        # sem um flush/refresh contra o Postgres, o objeto Python não teria
        # `enabled`/`billing_mode` populados antes do `_to_settings_out` abaixo.
        row = TenantBillingSettings(tenant_id=ctx.tenant_id, enabled=False, billing_mode="credits")
        session.add(row)

    if body.stripe_secret_key is not None:
        row.stripe_secret_key_encrypted = encrypt_tenant_secret(body.stripe_secret_key)
    if body.stripe_webhook_secret is not None:
        row.stripe_webhook_secret_encrypted = encrypt_tenant_secret(body.stripe_webhook_secret)
    if body.end_customer_tokens_per_credit is not None:
        row.end_customer_tokens_per_credit = body.end_customer_tokens_per_credit

    if body.enabled is True:
        if row.stripe_secret_key_encrypted is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Configure a secret key da Stripe antes de ativar a cobrança",
            )
        if not row.end_customer_tokens_per_credit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Configure a conversão de tokens por crédito antes de ativar a cobrança",
            )
    if body.enabled is not None:
        row.enabled = body.enabled

    await session.commit()
    return _to_settings_out(row)
