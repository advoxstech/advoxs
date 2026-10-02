import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KnowledgeBaseFile(Base):
    """Arquivo da base de conhecimento do escritório (tenant-scoped)."""

    __tablename__ = "knowledge_base_files"
    __table_args__ = (
        CheckConstraint("status IN ('processing', 'ready', 'error')", name="status"),
        CheckConstraint(
            "category IN ("
            "'artigos_cientificos', 'materias_jornalisticas', 'decisoes_tribunais', "
            "'livros_digitais', 'processos_judiciais', 'modelos_contratos', "
            "'pecas_processuais', 'nao_selecionaveis'"
            ") OR category IS NULL",
            name="category",
        ),
        Index(
            "uq_knowledge_base_files_tenant_filename",
            "tenant_id",
            "filename",
            unique=True,
            postgresql_where=text("superseded_at IS NULL AND replaces_file_id IS NULL"),
        ),
        Index(
            "uq_kb_drive_active",
            "tenant_id",
            "drive_file_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL AND replaces_file_id IS NULL"),
        ),
        Index("uq_kb_pending_replacement", "replaces_file_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'processing'"))
    error_message: Mapped[str | None] = mapped_column(Text)
    drive_file_id: Mapped[str | None] = mapped_column(String(200))
    drive_version: Mapped[str | None] = mapped_column(String(100))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A versão anterior permanece disponível para as fontes de respostas antigas.
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaces_file_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("knowledge_base_files.id", ondelete="RESTRICT")
    )
    # Categoria fixa (POP GVA Digital) — só organização/visual, não afeta a
    # busca do agente. NULL = "sem categoria" (arquivos legados ou upload sem
    # categoria escolhida).
    category: Mapped[str | None] = mapped_column(String)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
