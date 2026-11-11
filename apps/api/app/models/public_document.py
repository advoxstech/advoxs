import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PublicDocument(Base):
    """Documento da base central da Advoxs (global): legislação e jurisprudência
    lidas uma vez e copiadas para os escritórios pelos pacotes de especialidade."""

    __tablename__ = "public_documents"
    __table_args__ = (CheckConstraint("status IN ('processing', 'ready', 'error')", name="status"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Numeração permanente da base (1.01, 2.03a, Z.1).
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pages_total: Mapped[int | None] = mapped_column(Integer)
    partial_pages: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'processing'"))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class SpecialtyPackageDocument(Base):
    """Composição do pacote de uma especialidade (global)."""

    __tablename__ = "specialty_package_documents"

    specialty: Mapped[str] = mapped_column(String, primary_key=True)
    public_document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("public_documents.id", ondelete="CASCADE"), primary_key=True, index=True
    )
