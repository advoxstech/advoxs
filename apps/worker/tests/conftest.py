import os

# Envs mínimas para importar a aplicação em testes unitários (sem Postgres/Redis reais).
os.environ.setdefault(
    "APP_DATABASE_URL", "postgresql+asyncpg://advoxs_app:test@localhost:5432/test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
