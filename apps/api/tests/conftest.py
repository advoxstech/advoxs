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

import pytest  # noqa: E402

import app.core.queue as queue_module  # noqa: E402
import app.core.redis as redis_module  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_global_singletons():
    """app.core.redis/app.core.queue guardam um client/pool global, criado
    sob demanda e só fechado no lifespan de verdade do FastAPI (main.py).

    Sem resetar entre testes, um teste que aciona get_redis()/get_arq_pool()
    de verdade (sem mockar) deixa o singleton preso ao event loop function-
    scoped DAQUELE teste — quando outro teste, rodando depois, passa pelo
    lifespan de verdade (`with TestClient(app)`), o close_redis()/
    close_arq_pool() correspondente quebra tentando fechar uma conexão de um
    loop já fechado (`RuntimeError: Event loop is closed`). Só reseta DEPOIS
    do teste: os testes que mockam get_redis via monkeypatch continuam
    funcionando normalmente (o autouse não interfere no que roda durante o
    teste, só limpa o estado global ao final)."""
    yield
    redis_module._client = None
    queue_module._pool = None
