import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_phone(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"\+?[0-9 ()\-\.]+", value):
        raise ValueError("Informe um número com código do país, DDD e telefone.")
    digits = re.sub(r"\D", "", value)
    if digits.startswith("00"):
        digits = digits[2:]
    if not re.fullmatch(r"[1-9][0-9]{7,14}", digits):
        raise ValueError("Informe um número com código do país, DDD e telefone.")
    return digits


class TagInput(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    color: str = Field(default="#2563EB", pattern=r"^#[0-9a-fA-F]{6}$")

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Informe o nome da tag.")
        return value


class TagOut(TagInput):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class ContactInput(BaseModel):
    name: str = Field(default="", max_length=120)
    phone_number: str = Field(max_length=40)
    human_only: bool = False
    tag_ids: list[uuid.UUID] = Field(default_factory=list, max_length=30)

    _phone = field_validator("phone_number")(normalize_phone)


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    phone_number: str
    human_only: bool
    tags: list[TagOut]
