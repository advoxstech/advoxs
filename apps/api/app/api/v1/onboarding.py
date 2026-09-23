"""Acompanhamento opcional da configuração inicial de cada tenant."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.models import Agent, Conversation, KnowledgeBaseFile, Message, Tenant, WhatsAppNumber
from app.schemas.onboarding import OnboardingOut, OnboardingStepOut

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


async def _exists(session: AsyncSession, statement) -> bool:
    return await session.scalar(statement) is not None


@router.get("")
async def get_onboarding(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> OnboardingOut:
    tenant = await session.get(Tenant, ctx.tenant_id)
    has_agent = await _exists(
        session, select(Agent.id).where(Agent.tenant_id == ctx.tenant_id).limit(1)
    )
    has_whatsapp = await _exists(
        session,
        select(WhatsAppNumber.id)
        .where(
            WhatsAppNumber.tenant_id == ctx.tenant_id,
            WhatsAppNumber.status == "connected",
        )
        .limit(1),
    )
    has_knowledge = await _exists(
        session,
        select(KnowledgeBaseFile.id)
        .where(
            KnowledgeBaseFile.tenant_id == ctx.tenant_id,
            KnowledgeBaseFile.status == "ready",
        )
        .limit(1),
    )
    has_test_reply = await _exists(
        session,
        select(Message.id)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.tenant_id == ctx.tenant_id,
            Conversation.is_test.is_(True),
            Message.sender_type == "agent",
        )
        .limit(1),
    )
    has_real_conversation = await _exists(
        session,
        select(Conversation.id)
        .where(
            Conversation.tenant_id == ctx.tenant_id,
            Conversation.is_test.is_(False),
            Conversation.last_message_at.is_not(None),
        )
        .limit(1),
    )
    has_credits = tenant.credit_balance > 0

    steps = [
        OnboardingStepOut(
            key="agent",
            label="Agente configurado",
            description="Revise o nome, as instruções e o agente que inicia os atendimentos.",
            kind="main",
            completed=has_agent,
            action_label="Ver agentes",
            action_href="/agentes",
        ),
        OnboardingStepOut(
            key="whatsapp",
            label="WhatsApp conectado",
            description="Conecte um número pela Meta ou pela Z-API.",
            kind="main",
            completed=has_whatsapp,
            action_label="Configurar WhatsApp",
            action_href="/configuracoes/whatsapp",
        ),
        OnboardingStepOut(
            key="credits",
            label="Créditos disponíveis",
            description="Mantenha saldo disponível para os agentes responderem.",
            kind="main",
            completed=has_credits,
            action_label="Ver créditos",
            action_href="/creditos",
        ),
        OnboardingStepOut(
            key="knowledge_base",
            label="Base de conhecimento preparada",
            description="Adicione ao menos um arquivo e aguarde a indexação.",
            kind="recommended",
            completed=has_knowledge,
            action_label="Abrir base",
            action_href="/base-de-conhecimento",
        ),
        OnboardingStepOut(
            key="test_conversation",
            label="Primeiro teste respondido",
            description="Teste um agente antes de divulgar o número aos clientes.",
            kind="recommended",
            completed=has_test_reply,
            action_label="Testar agentes",
            action_href="/conversas?aba=testes",
        ),
        OnboardingStepOut(
            key="first_attendance",
            label="Primeiro atendimento recebido",
            description="Este marco aparece após a primeira conversa real.",
            kind="milestone",
            completed=has_real_conversation,
            action_label="Ver conversas",
            action_href="/conversas",
        ),
    ]
    completed_steps = sum(step.completed for step in steps)
    return OnboardingOut(
        completed=tenant.onboarding_completed_at is not None,
        main_configuration_complete=has_agent and has_whatsapp and has_credits,
        completed_steps=completed_steps,
        total_steps=len(steps),
        steps=steps,
    )


@router.post("/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_onboarding(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Oculta o lembrete opcional; não altera nem bloqueia o atendimento."""
    tenant = await session.get(Tenant, ctx.tenant_id)
    if tenant.onboarding_completed_at is None:
        tenant.onboarding_completed_at = datetime.now(UTC)
        await session.commit()
