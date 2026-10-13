import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OfficeMembership(Base):
    """Permissions belong to an office membership, never to the shared identity."""

    __tablename__ = "office_memberships"
    __table_args__ = (CheckConstraint("role IN ('admin','lawyer')", name="membership_role"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
