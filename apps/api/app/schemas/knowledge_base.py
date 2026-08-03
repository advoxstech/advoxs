import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Categorias fixas (POP GVA Digital) — só organização/visual na árvore de
# /base-de-conhecimento, não afeta a busca do agente. Mesmos valores do CHECK
# constraint em apps/api/app/models/knowledge_base_file.py e da migration
# 0026 (mantidos em sincronia manualmente, mesmo padrão já usado por outros
# enums via CHECK constraint neste projeto).
KnowledgeBaseCategory = Literal[
    "artigos_cientificos",
    "materias_jornalisticas",
    "decisoes_tribunais",
    "livros_digitais",
    "processos_judiciais",
    "modelos_contratos",
    "pecas_processuais",
    "nao_selecionaveis",
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


class KnowledgeBaseFileCategoryUpdate(BaseModel):
    category: KnowledgeBaseCategory | None = None
