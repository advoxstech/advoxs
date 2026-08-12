import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ZApiProvisioningRequest(Base):
    """Pedido do tenant pra Advoxs configurar a conexão Z-API por ele
    (fluxo manual, sem Programa de Parceiro/Integrador — ver
    app/services/zapi_connection.py). Tenant-scoped, sem UNIQUE por
    tenant_id: guarda histórico e permite um pedido novo depois de um
    anterior já resolvido."""

    __tablename__ = "zapi_provisioning_requests"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'fulfilled', 'dismissed')", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'pending'"))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
