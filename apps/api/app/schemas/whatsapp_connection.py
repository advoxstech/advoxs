from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ConnectWhatsAppRequest(BaseModel):
    phone_number_id: str = Field(min_length=1)
    waba_id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    pin: str = Field(pattern=r"^\d{6}$")


class ConnectZApiRequest(BaseModel):
    instance_id: str = Field(min_length=1)
    instance_token: str = Field(min_length=1)
    # Obrigatório em toda conexão nova — descoberto que a Z-API exige em
    # todas as contas, não só nas que ativaram "Client-Token por conta"
    # (suposição anterior, incorreta). Linhas antigas gravadas sem esse
    # campo continuam funcionando (ver app/clients/zapi.py::_headers).
    client_token: str = Field(min_length=1)


class WhatsAppConnectionOut(BaseModel):
    provider: Literal["meta", "zapi"]
    display_phone_number: str
    status: Literal["connected", "disconnected"]
    connected_at: datetime
    # True quando a instância Z-API foi provisionada manualmente pela Advoxs
    # (via painel de admin) em vez do próprio tenant ter colado as
    # credenciais — o painel do tenant usa isso pra pular o formulário e ir
    # direto pro QR code. Sempre False pra provider="meta".
    managed_by_advoxs: bool = False


class WebhookConfigOut(BaseModel):
    callback_url: str
    verify_token: str


class ZApiProvisioningRequestOut(BaseModel):
    status: Literal["pending", "fulfilled", "dismissed"]
    requested_at: datetime
