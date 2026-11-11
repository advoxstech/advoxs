import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

# Pastas da árvore de /base-de-conhecimento (A–F e Z). Mesmos valores de
# app/core/knowledge_tree.py e do CHECK constraint (migration 0053). O agente
# não consulta os arquivos de "nao_selecionaveis"; as demais só organizam.
KnowledgeBaseCategory = Literal[
    "livros_digitais",
    "artigos_cientificos",
    "processos_judiciais",
    "materias_jornalisticas",
    "modelos",
    "modelos_contratos",
    "pecas_processuais",
    "nao_selecionaveis",
    "constituicao",
    "legislacao_especifica",
    "decisoes_tribunais",
    "area_3",
    "area_4",
    "area_5",
    "area_6",
]


class KnowledgeBaseFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    size_bytes: int
    mime_type: str
    status: str
    error_message: str | None = None
    category: KnowledgeBaseCategory | None = None
    uploaded_at: datetime
    agent_ids: list[uuid.UUID] = Field(default_factory=list)
    drive_file_id: str | None = None
    imported_at: datetime | None = None
    replaces_file_id: uuid.UUID | None = None
    # Leitura parcial: páginas sem texto ou com leitura fraca mesmo com OCR.
    pages_total: int | None = None
    partial_pages: list[int] = Field(default_factory=list)
    # Cópia de um documento do pacote de especialidade (base Advoxs).
    from_package: bool = False


class KnowledgeBaseFileCategoryUpdate(BaseModel):
    category: KnowledgeBaseCategory | None = None


class DriveSelection(BaseModel):
    access_token: SecretStr = Field(repr=False)
    file_id: str = Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_-]+$")


class DriveImport(DriveSelection):
    agent_id: uuid.UUID
    category: KnowledgeBaseCategory | None = None
    expected_version: str = Field(min_length=1, max_length=100)
    replace_file_id: uuid.UUID | None = None
