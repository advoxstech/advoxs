import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AdminAuditLog(Base):
    """Registro de leitura de dado de um tenant específico por um platform_admin."""

    __tablename__ = "admin_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    platform_admin_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("platform_admins.id"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
