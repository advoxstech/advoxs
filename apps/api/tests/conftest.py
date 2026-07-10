import os

# Envs mínimas para importar a aplicação em testes unitários (sem Postgres/Redis reais).
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault(
    "APP_DATABASE_URL", "postgresql+asyncpg://advoxs_app:test@localhost:5432/advoxs"
)
os.environ.setdefault(
    "SYSTEM_DATABASE_URL", "postgresql+asyncpg://advoxs_system:test@localhost:5432/advoxs"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-secret-com-32-bytes-ou-mais-0123456789")
os.environ.setdefault("PLATFORM_JWT_SECRET", "test-platform-secret-com-32-bytes-ou-mais-0123456789")
os.environ.setdefault("META_VERIFY_TOKEN", "test-verify-token")
