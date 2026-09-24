import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "number"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("agents.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String)
    instructions: Mapped[str] = mapped_column(Text)
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    description: Mapped[str] = mapped_column(Text, server_default="")
    restored_from_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class AgentTestSession(Base):
    __tablename__ = "agent_test_sessions"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("agents.id", ondelete="CASCADE"))
    draft_revision: Mapped[int] = mapped_column(Integer)
    agents_snapshot: Mapped[list] = mapped_column(JSONB)
