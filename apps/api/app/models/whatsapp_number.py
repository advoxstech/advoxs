import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WhatsAppNumber(Base):
    """Número WhatsApp Business conectado (tenant-scoped, 1:1 com tenant)."""

    __tablename__ = "whatsapp_numbers"
    __table_args__ = (CheckConstraint("status IN ('connected', 'disconnected')", name="status"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, unique=True
    )
    # Unique: é a chave de resolução do webhook (phone_number_id -> tenant);
    # dois tenants nunca compartilham o mesmo número.
    phone_number_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    waba_id: Mapped[str] = mapped_column(String, nullable=False)
    display_phone_number: Mapped[str] = mapped_column(String, nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'connected'"))
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
