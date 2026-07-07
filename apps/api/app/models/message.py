import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Message(Base):
    """Mensagem de uma conversa (tenant-scoped, tenant_id denormalizado)."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("sender_type IN ('agent', 'human', 'contact')", name="sender_type"),
        # Queries do painel de conversas.
        Index("ix_messages_tenant_id_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    sender_type: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    media_url: Mapped[str | None] = mapped_column(String)
    media_type: Mapped[str | None] = mapped_column(String)
    # wamid da Meta — dedup de retries do webhook (só mensagens de contato).
    wa_message_id: Mapped[str | None] = mapped_column(String, unique=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer)
    credits_consumed: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
