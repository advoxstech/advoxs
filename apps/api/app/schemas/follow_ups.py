import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_RECIPIENTS = 100


class FollowUpTemplateOut(BaseModel):
    name: str
    language: str
    status: str
    category: str
    parameter_format: str
    header_text: str | None
    body_text: str
    footer_text: str | None
    param_names: list[str]
    supported: bool
    unsupported_reason: str | None
    rejected_reason: str | None


class FollowUpTemplatesOut(BaseModel):
    provider: Literal["meta", "zapi"]
    templates: list[FollowUpTemplateOut]


class FollowUpParam(BaseModel):
    source: Literal["text", "contact_name"] = "text"
    # Texto fixo, ou o nome usado quando o contato não tem nome cadastrado.
    value: str = Field(default="", max_length=500)

    @field_validator("value")
    @classmethod
    def clean_value(cls, value: str) -> str:
        # A Meta recusa parâmetros com quebra de linha ou tabulação.
        if "\n" in value or "\t" in value:
            raise ValueError("As variáveis não podem ter quebra de linha.")
        return value.strip()


class FollowUpCreate(BaseModel):
    contact_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_RECIPIENTS)
    conversation_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_RECIPIENTS)
    tag_ids: list[uuid.UUID] = Field(default_factory=list, max_length=30)
    template_name: str | None = Field(default=None, max_length=512)
    language: str | None = Field(default=None, max_length=20)
    params: list[FollowUpParam] = Field(default_factory=list, max_length=20)
    text: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def require_recipients(self):
        if not (self.contact_ids or self.conversation_ids or self.tag_ids):
            raise ValueError("Selecione ao menos um destinatário.")
        return self


class FollowUpRecipientOut(BaseModel):
    id: uuid.UUID
    phone: str
    contact_name: str
    status: str
    error: str | None
    sent_at: datetime | None
    conversation_id: uuid.UUID | None


class FollowUpBatchOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    created_by_name: str
    provider: str
    template_name: str | None
    text: str | None
    total: int
    sent: int
    failed: int
    skipped: int
    pending: int


class FollowUpBatchDetailOut(FollowUpBatchOut):
    recipients: list[FollowUpRecipientOut]
