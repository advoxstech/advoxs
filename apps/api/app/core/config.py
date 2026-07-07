from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    jwt_secret: str
    jwt_access_token_expires_minutes: int = 15
    jwt_refresh_token_expires_days: int = 30
    agents_service_url: str = "http://agents:8001"

    # Webhook da Meta (WhatsApp Cloud API)
    meta_verify_token: str = "changeme"
    # Quando setado, valida a assinatura X-Hub-Signature-256 de cada webhook.
    meta_app_secret: str = ""
    # Chave Fernet para cifrar whatsapp_numbers.access_token_encrypted
    # (mesma chave usada pelo worker para descriptografar).
    whatsapp_token_encryption_key: str = ""

    # Graph API da Meta (envio de mensagem no takeover humano)
    graph_api_base_url: str = "https://graph.facebook.com"
    graph_api_version: str = "v23.0"


settings = Settings()
