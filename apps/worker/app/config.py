from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str

    # Agents service (chamada interna com credenciais do tenant)
    agents_service_url: str = "http://agents:8001"
    agents_api_key: str = ""
    # Chave Fernet para descriptografar whatsapp_numbers.access_token_encrypted
    whatsapp_token_encryption_key: str = ""


settings = Settings()
