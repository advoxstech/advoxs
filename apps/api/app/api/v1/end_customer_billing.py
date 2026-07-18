"""Configuração da cobrança do cliente final (Stripe própria do tenant) e
pacotes de crédito que o tenant vende aos próprios clientes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.core.config import settings
from app.core.crypto import encrypt_tenant_secret
from app.models import EndCustomerCreditPackage, EndCustomerCreditTransaction, TenantBillingSettings
from app.schemas.end_customer_billing import (
    EndCustomerCreditPackageIn,
    EndCustomerCreditPackageOut,
    EndCustomerCreditPackageUpdate,
    EndCustomerSummaryOut,
    TenantBillingSettingsOut,
    TenantBillingSettingsUpdate,
)
from app.services.end_customer_billing import list_customers

router = APIRouter(prefix="/end-customer-billing", tags=["end-customer-billing"])


def _webhook_url_for(tenant_id: uuid.UUID) -> str:
    """URL completa do webhook por tenant, pro escritório colar no Dashboard
    da própria Stripe — mesmo padrão de GET /whatsapp/webhook-config (montada
    no backend via settings.api_public_url, nunca no client)."""
    base = settings.api_public_url.rstrip("/")
    return f"{base}/api/v1/webhooks/stripe/tenant/{tenant_id}"


def _to_settings_out(
    tenant_id: uuid.UUID, settings_row: TenantBillingSettings | None
) -> TenantBillingSettingsOut:
    if settings_row is None:
        return TenantBillingSettingsOut(
            tenant_id=tenant_id,
            enabled=False,
            billing_mode="credits",
            stripe_secret_key_configured=False,
            stripe_webhook_secret_configured=False,
            end_customer_tokens_per_credit=None,
            webhook_url=_webhook_url_for(tenant_id),
        )
    return TenantBillingSettingsOut(
        tenant_id=tenant_id,
        enabled=settings_row.enabled,
        billing_mode=settings_row.billing_mode,
        stripe_secret_key_configured=settings_row.stripe_secret_key_encrypted is not None,
        stripe_webhook_secret_configured=settings_row.stripe_webhook_secret_encrypted is not None,
        end_customer_tokens_per_credit=settings_row.end_customer_tokens_per_credit,
        webhook_url=_webhook_url_for(tenant_id),
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
    return _to_settings_out(ctx.tenant_id, await _get_settings_row(ctx, session))


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

    if body.enabled is True and row.stripe_secret_key_encrypted is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Configure a secret key da Stripe antes de ativar a cobrança",
        )
    if body.enabled is not None:
        row.enabled = body.enabled

    await session.commit()
    return _to_settings_out(ctx.tenant_id, row)


@router.get("/packages")
async def list_packages(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[EndCustomerCreditPackageOut]:
    result = await session.execute(
        select(EndCustomerCreditPackage).where(EndCustomerCreditPackage.tenant_id == ctx.tenant_id)
    )
    return [EndCustomerCreditPackageOut.model_validate(p) for p in result.scalars().all()]


@router.post("/packages", status_code=status.HTTP_201_CREATED)
async def create_package(
    body: EndCustomerCreditPackageIn,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> EndCustomerCreditPackageOut:
    package = EndCustomerCreditPackage(tenant_id=ctx.tenant_id, **body.model_dump())
    session.add(package)
    await session.commit()
    await session.refresh(package)
    return EndCustomerCreditPackageOut.model_validate(package)


async def _get_package(
    package_id: uuid.UUID, ctx: TenantContext, session: AsyncSession
) -> EndCustomerCreditPackage:
    package = await session.scalar(
        select(EndCustomerCreditPackage).where(
            EndCustomerCreditPackage.id == package_id,
            EndCustomerCreditPackage.tenant_id == ctx.tenant_id,
        )
    )
    if package is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pacote não encontrado")
    return package


@router.patch("/packages/{package_id}")
async def update_package(
    package_id: uuid.UUID,
    body: EndCustomerCreditPackageUpdate,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> EndCustomerCreditPackageOut:
    package = await _get_package(package_id, ctx, session)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(package, field, value)
    await session.commit()
    await session.refresh(package)
    return EndCustomerCreditPackageOut.model_validate(package)


@router.delete("/packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_package(
    package_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    package = await _get_package(package_id, ctx, session)
    used = await session.scalar(
        select(EndCustomerCreditTransaction.id).where(
            EndCustomerCreditTransaction.end_customer_credit_package_id == package_id
        )
    )
    if used is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pacote já usado em compras — desative em vez de excluir",
        )
    await session.delete(package)
    await session.commit()


@router.get("/customers")
async def list_end_customers(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[EndCustomerSummaryOut]:
    return await list_customers(session, ctx.tenant_id, limit, offset)
