"""Manual follow-up batches: one office message (Meta template or Z-API text)
sent to selected contacts, delivered one recipient at a time by the worker."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FollowUpBatch(Base):
    __tablename__ = "follow_up_batches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), index=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_name: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    provider: Mapped[str] = mapped_column(String(10))
    # Snapshot of the approved Meta template used: name, language, parameter_format,
    # header_text, body_text and param_names. NULL for Z-API batches.
    template: Mapped[dict | None] = mapped_column(JSONB)
    # One entry per template parameter: {"source": "text"|"contact_name", "value": str}.
    params: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # Texto livre da Z-API; o nome evita sombrear `text()` dentro da classe.
    message_text: Mapped[str | None] = mapped_column("text", Text)
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class FollowUpRecipient(Base):
    __tablename__ = "follow_up_recipients"
    __table_args__ = (UniqueConstraint("batch_id", "phone"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("follow_up_batches.id", ondelete="CASCADE")
    )
    # The FK to contacts lives only in migration 0049: contacts are recreated by raw SQL
    # in the contacts integration tests, which an ORM-level FK would block.
    contact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    phone: Mapped[str] = mapped_column(String(15))
    contact_name: Mapped[str] = mapped_column(String(120), default="", server_default=text("''"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="SET NULL")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("messages.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(10), default="pending", server_default=text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
