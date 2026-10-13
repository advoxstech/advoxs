import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AttendanceEvent(Base):
    __tablename__ = "attendance_events"
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE")
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    actor_name: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    target_name: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
