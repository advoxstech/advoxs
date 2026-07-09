"""Métricas agregadas do painel de administração — leitura pura de toda a
plataforma, sem filtro por tenant_id."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CreditPackage,
    CreditTransaction,
    KnowledgeBaseFile,
    Message,
    Tenant,
    WhatsAppNumber,
)
from app.schemas.admin_dashboard import (
    AdminDashboardOut,
    CreditsSummary,
    KnowledgeBaseUsageSummary,
    LowBalanceTenant,
    NewTenantsPerDay,
    TenantsByStatus,
    WhatsappConnectedSummary,
)

LOW_BALANCE_LIMIT = 10
PERIOD_DAYS = 30


async def build_dashboard(session: AsyncSession) -> AdminDashboardOut:
    since = datetime.now(UTC) - timedelta(days=PERIOD_DAYS)

    tenants_total = await session.scalar(select(func.count(Tenant.id))) or 0

    by_status_rows = (
        await session.execute(select(Tenant.status, func.count(Tenant.id)).group_by(Tenant.status))
    ).all()
    by_status = dict(by_status_rows)
    tenants_by_status = TenantsByStatus(
        active=by_status.get("active", 0), suspended=by_status.get("suspended", 0)
    )

    new_tenants_rows = (
        await session.execute(
            select(func.date(Tenant.created_at), func.count(Tenant.id))
            .where(Tenant.created_at >= since)
            .group_by(func.date(Tenant.created_at))
            .order_by(func.date(Tenant.created_at))
        )
    ).all()
    new_tenants_last_30_days = [
        NewTenantsPerDay(day=day, count=count) for day, count in new_tenants_rows
    ]

    revenue_brl_last_30_days = await session.scalar(
        select(func.coalesce(func.sum(CreditPackage.price_brl), 0))
        .select_from(CreditTransaction)
        .join(CreditPackage, CreditTransaction.credit_package_id == CreditPackage.id)
        .where(CreditTransaction.type == "purchase", CreditTransaction.created_at >= since)
    ) or Decimal("0")

    sold = (
        await session.scalar(
            select(func.coalesce(func.sum(CreditTransaction.amount_credits), 0)).where(
                CreditTransaction.type == "purchase"
            )
        )
        or 0
    )
    consumed_negative = (
        await session.scalar(
            select(func.coalesce(func.sum(CreditTransaction.amount_credits), 0)).where(
                CreditTransaction.type == "consumption"
            )
        )
        or 0
    )
    credits_summary = CreditsSummary(sold=sold, consumed=abs(consumed_negative))

    messages_processed = await session.scalar(select(func.count(Message.id))) or 0
    agent_executions = (
        await session.scalar(select(func.count(Message.id)).where(Message.tokens_used.is_not(None)))
        or 0
    )
    tokens_consumed = (
        await session.scalar(select(func.coalesce(func.sum(Message.tokens_used), 0))) or 0
    )

    low_balance_rows = (
        await session.execute(
            select(Tenant.id, Tenant.name, Tenant.credit_balance)
            .order_by(Tenant.credit_balance.asc())
            .limit(LOW_BALANCE_LIMIT)
        )
    ).all()
    low_balance_tenants = [
        LowBalanceTenant(id=id_, name=name, credit_balance=balance)
        for id_, name, balance in low_balance_rows
    ]

    whatsapp_connected = (
        await session.scalar(
            select(func.count(WhatsAppNumber.id)).where(WhatsAppNumber.status == "connected")
        )
        or 0
    )
    whatsapp_summary = WhatsappConnectedSummary(connected=whatsapp_connected, total=tenants_total)

    kb_files = await session.scalar(select(func.count(KnowledgeBaseFile.id))) or 0
    kb_bytes = (
        await session.scalar(select(func.coalesce(func.sum(KnowledgeBaseFile.size_bytes), 0))) or 0
    )
    kb_usage = KnowledgeBaseUsageSummary(total_files=kb_files, total_size_bytes=kb_bytes)

    return AdminDashboardOut(
        tenants_total=tenants_total,
        tenants_by_status=tenants_by_status,
        new_tenants_last_30_days=new_tenants_last_30_days,
        revenue_brl_last_30_days=revenue_brl_last_30_days,
        credits_summary=credits_summary,
        messages_processed=messages_processed,
        agent_executions=agent_executions,
        tokens_consumed=tokens_consumed,
        low_balance_tenants=low_balance_tenants,
        whatsapp_connected=whatsapp_summary,
        knowledge_base_usage=kb_usage,
    )
