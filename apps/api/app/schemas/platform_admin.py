from pydantic import BaseModel, EmailStr


class PlatformAdminLoginRequest(BaseModel):
    email: EmailStr
    password: str


class PlatformRefreshRequest(BaseModel):
    refresh_token: str


class PlatformTokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
