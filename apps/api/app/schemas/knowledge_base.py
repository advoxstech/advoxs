import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeBaseFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    size_bytes: int
    mime_type: str
    status: str
    error_message: str | None = None
    uploaded_at: datetime
    agent_ids: list[uuid.UUID] = Field(default_factory=list)
