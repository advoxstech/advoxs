import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UrgencyKeyword(Base):
    """Palavra ou frase que marca a conversa como urgente quando aparece numa
    mensagem do contato (tenant-scoped, editável pelo escritório)."""

    __tablename__ = "urgency_keywords"
    __table_args__ = (UniqueConstraint("tenant_id", "normalized"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    keyword: Mapped[str] = mapped_column(String, nullable=False)
    normalized: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
