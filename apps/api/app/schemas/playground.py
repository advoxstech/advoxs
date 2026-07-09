import uuid

from pydantic import BaseModel, Field


class PlaygroundMessageRequest(BaseModel):
    tenant_id: uuid.UUID
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1)


class PlaygroundMessageOut(BaseModel):
    responses: list[str]
    tokens_used: int | None
    current_agent: str | None
    grouped: bool
