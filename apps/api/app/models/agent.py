import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Agent(Base):
    """Agente de IA próprio do tenant (tenant-scoped) — nome, instruções em
    texto livre e um marcador de ponto de entrada (recebe a primeira
    mensagem de conversas novas; exatamente 1 por tenant)."""

    __tablename__ = "agents"
    __table_args__ = (
        Index(
            "uq_agents_tenant_entry_point",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_entry_point = true"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    is_entry_point: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AgentKnowledgeBaseFile(Base):
    """Vínculo muitos-pra-muitos entre um agente e um arquivo da base de
    conhecimento do tenant — só isolado por RLS via join com `agents`
    (ver migration 0015), não tem `tenant_id` próprio."""

    __tablename__ = "agent_knowledge_base_files"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_base_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("knowledge_base_files.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
