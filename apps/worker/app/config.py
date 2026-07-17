from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # advoxs_app (RLS ativo) — mesmo valor de APP_DATABASE_URL usado pelo
    # api, ver migration 0008 no apps/api.
    app_database_url: str
    redis_url: str

    # Agents service (chamada interna com credenciais do tenant)
    agents_service_url: str = "http://agents:8001"
    agents_api_key: str = ""
    # Chave Fernet para descriptografar whatsapp_numbers.access_token_encrypted
    whatsapp_token_encryption_key: str = ""

    # Conversão de consumo: 1 crédito = N tokens (arredondamento sempre pra
    # cima). Valor de partida — calibrar com o custo real do LLM + margem.

    # Takeover humano: sem heartbeat do painel há mais que N segundos, a IA
    # reassume a conversa na chegada da próxima mensagem do contato.
    human_takeover_timeout_seconds: int = 180

    # api_rag (ingestão da base de conhecimento)
    rag_api_url: str = "http://api_rag:8000"
    rag_api_key: str = ""
    kb_upload_dir: str = "/data/kb_uploads"


settings = Settings()
