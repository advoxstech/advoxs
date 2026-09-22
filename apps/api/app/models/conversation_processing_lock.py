"""Bloqueio durável que serializa turnos de uma conversa."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ConversationProcessingLock(Base):
    """Impede dois workers de executar a IA para a mesma conversa."""

    __tablename__ = "conversation_processing_locks"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("inbound_message_jobs.id"), nullable=False
    )
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
