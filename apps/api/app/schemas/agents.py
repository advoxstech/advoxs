import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    instructions: str
    is_entry_point: bool
    created_at: datetime
    updated_at: datetime


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1)
    is_entry_point: bool = False


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    instructions: str | None = Field(default=None, min_length=1)
    is_entry_point: bool | None = None


class AgentKnowledgeBaseFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    knowledge_base_file_id: uuid.UUID


class AttachKnowledgeBaseFileIn(BaseModel):
    knowledge_base_file_id: uuid.UUID
