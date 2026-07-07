from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class DocumentoUsuario(Base):
    __tablename__ = "documentos_usuario"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Escritório dono do documento — todo acesso filtra por ele.
    tenant_id = Column(String, nullable=False, index=True)
    conversation_id = Column(String)
    nome = Column(String, nullable=False)
    extensao = Column(String, nullable=False)
    path_base = Column(String, nullable=False)  # ex: "/data/uploads/users" ou URL de bucket
    path_doc = Column(String, nullable=False)  # "documentos/usuario" — imutável
    criado_em = Column(DateTime, default=datetime.utcnow)


class DocumentoSistema(Base):
    __tablename__ = "documentos_sistema"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    id_drive = Column(String)
    base = Column(String)
    nome = Column(String, nullable=False)
    extensao = Column(String, nullable=False)
    path_base = Column(String, nullable=False)
    path_doc = Column(String, nullable=False)
    criado_em = Column(DateTime, default=datetime.utcnow)
