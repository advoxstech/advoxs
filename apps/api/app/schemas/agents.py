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


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    instructions: str | None = Field(default=None, min_length=1)


class AgentKnowledgeBaseFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    knowledge_base_file_id: uuid.UUID


class AttachKnowledgeBaseFileIn(BaseModel):
    knowledge_base_file_id: uuid.UUID


class DraftRevisionIn(BaseModel):
    expected_revision: int = Field(ge=0)


class AgentDraftIn(DraftRevisionIn):
    name: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1)


class AgentPublishIn(DraftRevisionIn):
    description: str = Field(default="", max_length=1000)


class AgentWorkspaceOut(BaseModel):
    published: AgentOut
    published_version: int
    draft_revision: int
    name: str
    instructions: str
    has_unpublished_changes: bool


class AgentVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    name: str
    instructions: str
    author_id: uuid.UUID | None
    author_name: str | None = None
    description: str
    restored_from_version: int | None
    created_at: datetime
