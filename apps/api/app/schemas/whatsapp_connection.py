from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ConnectWhatsAppRequest(BaseModel):
    phone_number_id: str = Field(min_length=1)
    waba_id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    pin: str = Field(pattern=r"^\d{6}$")


class WhatsAppConnectionOut(BaseModel):
    display_phone_number: str
    status: Literal["connected", "disconnected"]
    connected_at: datetime


class WebhookConfigOut(BaseModel):
    callback_url: str
    verify_token: str
