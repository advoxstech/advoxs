import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Conversation(Base):
    """Conversa de um contato com o escritório (tenant-scoped).

    Uma conversa por contato por tenant — o par (tenant_id, contact_phone_number)
    é único e espelha o thread_id usado no checkpoint do agents service.
    """

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("state IN ('agent', 'human')", name="state"),
        UniqueConstraint("tenant_id", "contact_phone_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    contact_phone_number: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'agent'"))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str | None] = mapped_column(Text)
    summary_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    human_last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
