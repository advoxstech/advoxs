from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Owner das tabelas — usada só pelo Alembic (DDL); a app não conecta
    # mais com esse papel em runtime.
    database_url: str
    # advoxs_app (RLS ativo) e advoxs_system (BYPASSRLS) — ver migration
    # 0008 e app/core/db.py.
    app_database_url: str
    system_database_url: str
    redis_url: str
    jwt_secret: str
    jwt_access_token_expires_minutes: int = 15
    jwt_refresh_token_expires_days: int = 30
    agents_service_url: str = "http://agents:8001"
    # Auth de serviço com o agents (playground de admin — o worker usa a
    # mesma env, mas cada serviço lê o próprio settings).
    agents_api_key: str = ""
    # Conversão de consumo: 1 crédito = N tokens (mesma fórmula do worker,
    # arredondamento sempre pra cima). Usado pelo débito de resumo de conversa.
    credit_tokens_per_credit: int = 1000

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

    # Base de conhecimento (upload → volume compartilhado → ingestão no api_rag)
    kb_upload_dir: str = "/data/kb_uploads"
    kb_max_file_size_bytes: int = 20 * 1024 * 1024
    kb_max_total_size_bytes: int = 500 * 1024 * 1024
    rag_api_url: str = "http://api_rag:8000"
    rag_api_key: str = ""

    # Logo do escritório — path fixo por tenant, sobrescrito a cada upload.
    logo_upload_dir: str = "/data/logo_uploads"
    logo_max_file_size_bytes: int = 2 * 1024 * 1024

    # Stripe (cadastro self-service — checkout de créditos, sem assinatura)
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # URL pública do `web`, usada para montar success_url/cancel_url do Checkout.
    web_app_url: str = "http://localhost:3000"

    # Platform admin (painel de administração da plataforma) — secret
    # separado do JWT_SECRET dos tenants, defesa em profundidade: um
    # segredo vazado nunca forja o outro tipo de token.
    platform_jwt_secret: str = ""


settings = Settings()
