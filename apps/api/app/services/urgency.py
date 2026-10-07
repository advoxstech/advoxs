"""Sinalização de conversas urgentes: palavras por agente + análise contextual."""

import re
import unicodedata
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, Conversation, UrgencyKeyword

# Lista viva pra tenants novos; a migration 0041 tem uma cópia congelada.
DEFAULT_URGENCY_KEYWORDS = [
    "preso",
    "presa",
    "prisão",
    "flagrante",
    "delegacia",
    "medida protetiva",
    "ameaça",
    "violência",
    "agressão",
    "audiência amanhã",
    "audiência hoje",
    "prazo vence hoje",
    "prazo vence amanhã",
    "intimação",
    "liminar",
    "despejo",
    "penhora",
    "bloqueio de conta",
    "leilão",
    "busca e apreensão",
    "mandado",
]

MAX_KEYWORDS_PER_AGENT = 100
MAX_REASON_LENGTH = 300


def normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_accents.split())


def match_keyword(text: str, keywords: list[UrgencyKeyword]) -> UrgencyKeyword | None:
    """Primeira palavra-chave presente como palavra/frase inteira no texto."""
    normalized_text = normalize(text)
    if not normalized_text:
        return None
    for keyword in keywords:
        pattern = r"(?<!\w)" + re.escape(keyword.normalized) + r"(?!\w)"
        if re.search(pattern, normalized_text):
            return keyword
    return None


def mark_urgent(
    conversation: Conversation,
    reason: str,
    source: str,
    *,
    agent_id: uuid.UUID | None = None,
) -> None:
    """Idempotente: mantém o horário da primeira sinalização ainda aberta.

    O motivo só é sobrescrito quando vem do agente (mais descritivo que uma
    palavra-chave) ou quando a conversa ainda não estava urgente.
    """
    reason = reason.strip()[:MAX_REASON_LENGTH]
    if conversation.urgent_since is None:
        conversation.urgent_since = datetime.now(UTC)
        conversation.urgent_reason = reason
        conversation.urgent_source = source
        conversation.urgent_agent_id = agent_id if source == "keyword" else None
    elif source == "agent":
        conversation.urgent_reason = reason
        conversation.urgent_source = source
        conversation.urgent_agent_id = None


def clear_urgent(conversation: Conversation) -> None:
    conversation.urgent_since = None
    conversation.urgent_reason = None
    conversation.urgent_source = None
    conversation.urgent_agent_id = None


async def list_keywords(
    session: AsyncSession, tenant_id: uuid.UUID, agent_id: uuid.UUID
) -> list[UrgencyKeyword]:
    return list(
        await session.scalars(
            select(UrgencyKeyword)
            .where(
                UrgencyKeyword.tenant_id == tenant_id,
                UrgencyKeyword.agent_id == agent_id,
            )
            .order_by(UrgencyKeyword.created_at, UrgencyKeyword.keyword)
        )
    )


async def flag_contact_message(
    session: AsyncSession,
    conversation: Conversation,
    content: str | None,
    *,
    agent_id: uuid.UUID | None = None,
) -> None:
    """Checa a mensagem contra a lista do agente responsável no recebimento."""
    if not content:
        return
    selected_agent_id = agent_id or getattr(conversation, "current_agent_id", None)
    if selected_agent_id is None:
        selected_agent_id = await session.scalar(
            select(Agent.id).where(
                Agent.tenant_id == conversation.tenant_id,
                Agent.is_entry_point.is_(True),
            )
        )
    if selected_agent_id is None:
        return
    keywords = await list_keywords(session, conversation.tenant_id, selected_agent_id)
    keyword = match_keyword(content, keywords)
    if keyword is not None:
        mark_urgent(
            conversation,
            f'Palavra-chave: "{keyword.keyword}"',
            "keyword",
            agent_id=selected_agent_id,
        )


def build_default_urgency_keywords(
    tenant_id: uuid.UUID, agent_id: uuid.UUID
) -> list[UrgencyKeyword]:
    return [
        UrgencyKeyword(
            tenant_id=tenant_id,
            agent_id=agent_id,
            keyword=keyword,
            normalized=normalize(keyword),
        )
        for keyword in DEFAULT_URGENCY_KEYWORDS
    ]


async def restore_default_keywords(
    session: AsyncSession, tenant_id: uuid.UUID, agent_id: uuid.UUID
) -> None:
    await session.execute(
        insert(UrgencyKeyword)
        .values(
            [
                {
                    "tenant_id": tenant_id,
                    "agent_id": agent_id,
                    "keyword": keyword,
                    "normalized": normalize(keyword),
                }
                for keyword in DEFAULT_URGENCY_KEYWORDS
            ]
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "agent_id", "normalized"])
    )
