from pydantic import BaseModel, Field


class ProfileOut(BaseModel):
    tenant_name: str
    email_contato: str
    has_logo: bool
    user_name: str
    user_email: str


class ProfileUpdateRequest(BaseModel):
    tenant_name: str = Field(min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)
