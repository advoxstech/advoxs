from pydantic_settings import BaseSettings, SettingsConfigDict

from config_validation import validate_environment

validate_environment("worker")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    app_env: str = "development"

    # advoxs_app (RLS ativo) — mesmo valor de APP_DATABASE_URL usado pelo
    # api, ver migration 0008 no apps/api.
    app_database_url: str
    # Papel interno, usado somente para recuperar a caixa de saída pendente
    # entre todos os tenants. A variável já é compartilhada pelo Compose.
    system_database_url: str
    redis_url: str

    # Agents service (chamada interna com credenciais do tenant)
    agents_service_url: str = "http://agents:8001"
    agents_api_key: str = ""
    # Chave Fernet para descriptografar whatsapp_numbers.access_token_encrypted
    whatsapp_token_encryption_key: str = ""

    # Graph API (envio direto de mensagens do billing gate, sem passar pelo agents).
    graph_api_base_url: str = "https://graph.facebook.com"
    graph_api_version: str = "v23.0"

    # api (endpoint interno de checkout do cliente final — mesma chave
    # configurada em apps/api/app/core/config.py::INTERNAL_SERVICE_KEY).
    api_url: str = "http://api:8000"
    web_app_url: str = "http://localhost:3000"
    internal_service_key: str = ""

    # Conversão de consumo: 1 crédito = N tokens (arredondamento sempre pra
    # cima). Valor de partida — calibrar com o custo real do LLM + margem.

    # api_rag (ingestão da base de conhecimento)
    rag_api_url: str = "http://api_rag:8000"
    rag_api_key: str = ""
    kb_upload_dir: str = "/data/kb_uploads"

    # Anexo (PDF/DOCX/TXT) recebido do contato via WhatsApp e ingerido na
    # base de conhecimento pessoal dele — mesmo teto já usado na base de
    # conhecimento do escritório (ver apps/api/app/core/config.py,
    # kb_max_file_size_bytes). Env: ATTACHMENT_MAX_BYTES.
    attachment_max_bytes: int = 60 * 1024 * 1024

    # WhatsApp oficial via Z-API Omni (workspace da Advoxs; mesma chave do api).
    omni_secret_key: str = ""
    omni_compat_base_url: str = "https://zapi.omni.z-api.io"
    omni_api_base_url: str = "https://api.omni.z-api.io"

    # Notificação por e-mail (Gmail SMTP) quando o saldo de créditos de um
    # tenant zera — ver app/email_notifications.py. Mesmas 3 variáveis já
    # configuradas no apps/api (mesma conta Gmail, serviço diferente lendo
    # o próprio .env). Sem as 3, o envio é pulado em silêncio.
    gmail_smtp_user: str = ""
    gmail_smtp_app_password: str = ""
    admin_notification_email: str = ""

    # Transcrição de áudio do contato (ver app/tasks/media.py). Desligada,
    # o áudio segue o comportamento anterior (nota "formato não suportado").
    audio_transcription_enabled: bool = False
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    transcription_model: str = "gpt-4o-transcribe"
    transcription_timeout_seconds: float = 120
    transcription_max_concurrency: int = 2
    transcription_uncertain_threshold: float = 0.5
    transcription_usd_per_minute: float = 0.006
    audio_max_duration_seconds: int = 600
    # Teto do arquivo enviado à OpenAI (a API recusa acima de 25 MB), menor
    # que attachment_max_bytes (60 MB, documentos).
    audio_max_bytes: int = 25 * 1024 * 1024
    media_max_attempts: int = 3


def validate_transcription_config(s: Settings) -> None:
    if s.audio_transcription_enabled and not s.openai_api_key.strip():
        raise RuntimeError("AUDIO_TRANSCRIPTION_ENABLED=true exige OPENAI_API_KEY")


settings = Settings()
