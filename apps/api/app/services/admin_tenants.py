"""Listagem e detalhe de tenants para o painel de administração.

Leitura de um tenant específico (get_tenant_detail) é auditada em
AdminAuditLog — implementa a exigência do CLAUDE.md.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AdminAuditLog,
    CreditTransaction,
    KnowledgeBaseFile,
    Tenant,
    WhatsAppNumber,
)
from app.schemas.admin_tenants import (
    CreditTransactionOut,
    KnowledgeBaseFileSummaryOut,
    TenantDetailOut,
    TenantListItemOut,
)

RECENT_TRANSACTIONS_LIMIT = 20


async def list_tenants(session: AsyncSession, limit: int, offset: int) -> list[TenantListItemOut]:
    tenants = (
        (
            await session.execute(
                select(Tenant).order_by(Tenant.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )

    connected_ids = set(
        (
            await session.execute(
                select(WhatsAppNumber.tenant_id).where(WhatsAppNumber.status == "connected")
            )
        )
        .scalars()
        .all()
    )

    return [
        TenantListItemOut(
            id=t.id,
            name=t.name,
            status=t.status,
            credit_balance=t.credit_balance,
            created_at=t.created_at,
            whatsapp_connected=t.id in connected_ids,
        )
        for t in tenants
    ]


async def get_tenant_detail(
    session: AsyncSession, tenant_id: uuid.UUID, platform_admin_id: uuid.UUID
) -> TenantDetailOut | None:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        return None

    transactions = (
        (
            await session.execute(
                select(CreditTransaction)
                .where(CreditTransaction.tenant_id == tenant_id)
                .order_by(CreditTransaction.created_at.desc())
                .limit(RECENT_TRANSACTIONS_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    files = (
        (
            await session.execute(
                select(KnowledgeBaseFile)
                .where(KnowledgeBaseFile.tenant_id == tenant_id)
                .order_by(KnowledgeBaseFile.uploaded_at.desc())
            )
        )
        .scalars()
        .all()
    )

    session.add(AdminAuditLog(platform_admin_id=platform_admin_id, tenant_id=tenant_id))
    await session.commit()

    return TenantDetailOut(
        id=tenant.id,
        name=tenant.name,
        email_contato=tenant.email_contato,
        status=tenant.status,
        credit_balance=tenant.credit_balance,
        created_at=tenant.created_at,
        recent_transactions=[CreditTransactionOut.model_validate(t) for t in transactions],
        knowledge_base_files=[KnowledgeBaseFileSummaryOut.model_validate(f) for f in files],
    )
