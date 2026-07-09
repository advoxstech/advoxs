# Painel de Administração da Plataforma Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Área de back-office (`/admin`) para a Advoxs gerenciar/visualizar todos os tenants — dashboard agregado (vendas, consumo, crescimento) + lista/detalhe de tenants — com autenticação própria, totalmente isolada da sessão de tenant.

**Architecture:** Autenticação do `platform_admin` com JWT/secret/cookies separados dos de tenant (defesa em profundidade — nenhum dos dois tipos de sessão nunca é válido pro outro domínio). Dashboard e listagem são queries agregadas simples (sem view materializada); o detalhe de um tenant específico grava uma linha de auditoria (`admin_audit_logs`). Frontend com proxy (`/api/admin-backend/*`) e middleware isolados dos já existentes para tenant.

**Tech Stack:** FastAPI + SQLAlchemy async + PyJWT (api), Next.js 15 App Router (web), pytest + Vitest.

## Global Constraints

- **Isolamento total de sessão**: secret (`PLATFORM_JWT_SECRET`), `type` de token (`platform_access`/`platform_refresh`), prefixo de blacklist no Redis (`platform_auth:blacklist:`), cookies (`platform_access_token`/`platform_refresh_token`) e proxy (`/api/admin-backend/*`) — tudo separado do que já existe para tenant. Nunca reaproveitar `JWT_SECRET`, `ACCESS_TOKEN_COOKIE`, `/api/backend/*` ou `auth:blacklist:`.
- **Sem ações nesta entrega** — só leitura. Suspender/creditar ficam de fora.
- **RLS inerte por conexão como owner**: as rotas do admin usam `get_session` simples (sem `app.tenant_id`), porque o `api` conecta como owner das tabelas hoje — isso é documentado como simplificação temporária, não permanente (ver Task 7).
- **Auditoria só no detalhe de um tenant específico** (`GET /platform-admin/tenants/{id}`) — grava uma linha em `admin_audit_logs` a cada chamada. Dashboard e listagem agregada NÃO precisam de auditoria.
- **Provisionamento do `platform_admin` via script** (`scripts/seed_platform_admin.py`), não cadastro público.
- Métricas calculadas na hora (queries agregadas), sem cache/view materializada.
- Mensagens/comentários em pt-BR com acentuação correta.
- Commits: Conventional Commits em pt-BR. Testes: `uv run pytest tests/unit` (api), `pnpm test` (web). Lint: `uv run ruff check .` (api).
- **Sem mudança no `docker-compose.yml`**: `PLATFORM_JWT_SECRET` chega ao container `api` via `env_file: .env`, mesmo padrão de `STRIPE_SECRET_KEY`.

---

### Task 1: `api` — autenticação do `platform_admin`

**Files:**
- Create: `apps/api/alembic/versions/0004_admin_audit_logs.py`
- Create: `apps/api/app/models/admin_audit_log.py`
- Modify: `apps/api/app/models/__init__.py`
- Modify: `apps/api/app/core/config.py`
- Create: `apps/api/app/core/platform_security.py`
- Modify: `apps/api/app/api/deps.py`
- Create: `apps/api/app/schemas/platform_admin.py`
- Create: `apps/api/app/services/platform_admin_auth.py`
- Create: `apps/api/app/api/v1/platform_admin/__init__.py`
- Create: `apps/api/app/api/v1/platform_admin/auth.py`
- Create: `apps/api/scripts/seed_platform_admin.py`
- Modify: `apps/api/app/api/v1/router.py`
- Modify: `.env.example`
- Test: `apps/api/tests/unit/test_platform_admin_auth_routes.py`

**Interfaces:**
- Produces: `class AdminAuditLog(Base)` (model, tabela `admin_audit_logs`: `id`, `platform_admin_id` FK, `tenant_id` FK, `created_at`); `create_platform_access_token(admin_id, role) -> str`, `create_platform_refresh_token(admin_id) -> str`, `decode_platform_token(token) -> dict` em `app.core.platform_security`; `class PlatformAdminContext(BaseModel)` (`admin_id: uuid.UUID`, `role: str`) e `get_current_platform_admin` em `app.api.deps`; `POST /api/v1/platform-admin/auth/{login,refresh,logout}`.

- [ ] **Step 1: Migração — tabela `admin_audit_logs`**

Criar `apps/api/alembic/versions/0004_admin_audit_logs.py`:

```python
"""tabela admin_audit_logs (auditoria de leitura de tenant específico)

Toda chamada a GET /platform-admin/tenants/{id} grava uma linha aqui —
implementa a exigência de auditoria do CLAUDE.md (super-admin lendo dado de
um tenant específico atravessa o isolamento normal por tenant_id).

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("platform_admin_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["platform_admin_id"],
            ["platform_admins.id"],
            name=op.f("fk_admin_audit_logs_platform_admin_id_platform_admins"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_admin_audit_logs_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit_logs")),
    )
    op.create_index(
        op.f("ix_admin_audit_logs_platform_admin_id"), "admin_audit_logs", ["platform_admin_id"]
    )
    op.create_index(op.f("ix_admin_audit_logs_tenant_id"), "admin_audit_logs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_audit_logs_tenant_id"), table_name="admin_audit_logs")
    op.drop_index(op.f("ix_admin_audit_logs_platform_admin_id"), table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")
```

- [ ] **Step 2: Model**

Criar `apps/api/app/models/admin_audit_log.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AdminAuditLog(Base):
    """Registro de leitura de dado de um tenant específico por um platform_admin."""

    __tablename__ = "admin_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    platform_admin_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("platform_admins.id"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
```

Em `apps/api/app/models/__init__.py`, adicionar o import e exportar em `__all__` (mantendo ordem alfabética):

```python
from app.models.admin_audit_log import AdminAuditLog
```

E em `__all__`, adicionar `"AdminAuditLog",` (ordem alfabética, antes de `"Base"` se vier antes — confira a ordem atual do arquivo e insira no lugar certo).

- [ ] **Step 3: Config**

Em `apps/api/app/core/config.py`, adicionar ao final da classe `Settings`:

```python
    # Platform admin (painel de administração da plataforma) — secret
    # separado do JWT_SECRET dos tenants, defesa em profundidade: um
    # segredo vazado nunca forja o outro tipo de token.
    platform_jwt_secret: str = ""
```

- [ ] **Step 4: `.env.example`**

No `.env.example` da raiz, adicionar na seção de Stripe/auth (junto das outras envs de auth):

```
# Platform admin (painel de administração /admin) — secret separado do JWT_SECRET
PLATFORM_JWT_SECRET=changeme-platform
```

- [ ] **Step 5: JWT do platform_admin**

Criar `apps/api/app/core/platform_security.py`:

```python
"""JWT do platform_admin — secret e claims separados dos tokens de tenant
(defesa em profundidade: um segredo vazado nunca forja o outro tipo de token)."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import settings

ALGORITHM = "HS256"
ACCESS_EXPIRES_MINUTES = 15
REFRESH_EXPIRES_DAYS = 30


def create_platform_access_token(admin_id: str, role: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(admin_id),
        "role": role,
        "type": "platform_access",
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_EXPIRES_MINUTES),
    }
    return jwt.encode(payload, settings.platform_jwt_secret, algorithm=ALGORITHM)


def create_platform_refresh_token(admin_id: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(admin_id),
        "jti": str(uuid.uuid4()),
        "type": "platform_refresh",
        "iat": now,
        "exp": now + timedelta(days=REFRESH_EXPIRES_DAYS),
    }
    return jwt.encode(payload, settings.platform_jwt_secret, algorithm=ALGORITHM)


def decode_platform_token(token: str) -> dict:
    """Decodifica e valida assinatura/expiração. Levanta jwt.PyJWTError se inválido."""
    return jwt.decode(token, settings.platform_jwt_secret, algorithms=[ALGORITHM])
```

- [ ] **Step 6: Schemas**

Criar `apps/api/app/schemas/platform_admin.py`:

```python
from pydantic import BaseModel, EmailStr


class PlatformAdminLoginRequest(BaseModel):
    email: EmailStr
    password: str


class PlatformRefreshRequest(BaseModel):
    refresh_token: str


class PlatformTokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
```

- [ ] **Step 7: Escrever os testes que falham**

Criar `apps/api/tests/unit/test_platform_admin_auth_routes.py`:

```python
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_session
from app.core.redis import get_redis
from app.core.security import hash_password
from app.main import app
from app.models import PlatformAdmin
from app.services.platform_admin_auth import BLACKLIST_PREFIX

ADMIN_ID = uuid.uuid4()
PASSWORD = "senha-secreta"


def _admin() -> MagicMock:
    admin = MagicMock(spec=PlatformAdmin)
    admin.id = ADMIN_ID
    admin.role = "superadmin"
    admin.password_hash = hash_password(PASSWORD)
    return admin


@pytest.fixture
def session():
    return AsyncMock()


@pytest.fixture
def redis():
    mock = AsyncMock()
    mock.exists.return_value = 0
    return mock


@pytest.fixture
def client(session, redis):
    async def override_session():
        yield session

    async def override_redis():
        return redis

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_redis] = override_redis
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestLogin:
    def test_login_valido_retorna_par_de_tokens(self, client, session) -> None:
        session.scalar.return_value = _admin()

        response = client.post(
            "/api/v1/platform-admin/auth/login", json={"email": "a@b.com", "password": PASSWORD}
        )

        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body and "refresh_token" in body

    def test_senha_incorreta_retorna_401(self, client, session) -> None:
        session.scalar.return_value = _admin()

        response = client.post(
            "/api/v1/platform-admin/auth/login", json={"email": "a@b.com", "password": "errada"}
        )

        assert response.status_code == 401

    def test_email_inexistente_retorna_401(self, client, session) -> None:
        session.scalar.return_value = None

        response = client.post(
            "/api/v1/platform-admin/auth/login", json={"email": "x@y.com", "password": PASSWORD}
        )

        assert response.status_code == 401


class TestRefresh:
    def test_refresh_valido_rotaciona_e_blacklista_o_antigo(self, client, session, redis) -> None:
        session.scalar.return_value = _admin()
        session.get.return_value = _admin()
        login = client.post(
            "/api/v1/platform-admin/auth/login", json={"email": "a@b.com", "password": PASSWORD}
        )
        old_refresh = login.json()["refresh_token"]

        response = client.post(
            "/api/v1/platform-admin/auth/refresh", json={"refresh_token": old_refresh}
        )

        assert response.status_code == 200
        redis.set.assert_awaited_once()
        key = redis.set.await_args.args[0]
        assert key.startswith(BLACKLIST_PREFIX)

    def test_refresh_blacklistado_retorna_401(self, client, redis) -> None:
        redis.exists.return_value = 1
        from app.core.platform_security import create_platform_refresh_token

        token = create_platform_refresh_token(str(ADMIN_ID))

        response = client.post("/api/v1/platform-admin/auth/refresh", json={"refresh_token": token})

        assert response.status_code == 401


class TestLogout:
    def test_logout_blacklista_o_refresh(self, client, redis) -> None:
        from app.core.platform_security import create_platform_refresh_token

        token = create_platform_refresh_token(str(ADMIN_ID))

        response = client.post("/api/v1/platform-admin/auth/logout", json={"refresh_token": token})

        assert response.status_code == 204
        redis.set.assert_awaited_once()
```

- [ ] **Step 8: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_platform_admin_auth_routes.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'app.services.platform_admin_auth'`.

- [ ] **Step 9: Service**

Criar `apps/api/app/services/platform_admin_auth.py`:

```python
"""Login, refresh com rotação e logout do platform_admin.

Isolado do auth de tenant: secret e prefixo de blacklist próprios — nunca
compartilha token nem estado com app.services.auth (login de tenant).
"""

import logging
import uuid
from datetime import UTC, datetime

import jwt
from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.platform_security import (
    create_platform_access_token,
    create_platform_refresh_token,
    decode_platform_token,
)
from app.core.security import hash_password, verify_password
from app.models import PlatformAdmin

logger = logging.getLogger(__name__)

BLACKLIST_PREFIX = "platform_auth:blacklist:"

# Hash de comparação para e-mail inexistente — iguala o tempo de resposta.
_DUMMY_HASH = hash_password("dummy-timing-equalizer")

_CREDENCIAIS_INVALIDAS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas"
)


async def login(email: str, password: str, session: AsyncSession) -> tuple[str, str]:
    admin = await session.scalar(select(PlatformAdmin).where(PlatformAdmin.email == email))
    if admin is None:
        verify_password(password, _DUMMY_HASH)
        raise _CREDENCIAIS_INVALIDAS
    if not verify_password(password, admin.password_hash):
        raise _CREDENCIAIS_INVALIDAS

    logger.info("Login de platform_admin | admin=%s", admin.id)
    return (
        create_platform_access_token(str(admin.id), admin.role),
        create_platform_refresh_token(str(admin.id)),
    )


async def refresh(refresh_token: str, session: AsyncSession, redis: Redis) -> tuple[str, str]:
    payload = _decode_refresh(refresh_token)

    if await redis.exists(f"{BLACKLIST_PREFIX}{payload['jti']}"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revogado"
        )

    try:
        admin_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        raise _CREDENCIAIS_INVALIDAS
    admin = await session.get(PlatformAdmin, admin_id)
    if admin is None:
        raise _CREDENCIAIS_INVALIDAS

    await _blacklist(redis, payload)

    return (
        create_platform_access_token(str(admin.id), admin.role),
        create_platform_refresh_token(str(admin.id)),
    )


async def logout(refresh_token: str, redis: Redis) -> None:
    payload = _decode_refresh(refresh_token)
    await _blacklist(redis, payload)


def _decode_refresh(token: str) -> dict:
    try:
        payload = decode_platform_token(token)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token inválido"
        )
    if payload.get("type") != "platform_refresh" or "jti" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token inválido"
        )
    return payload


async def _blacklist(redis: Redis, payload: dict) -> None:
    ttl = int(payload["exp"] - datetime.now(UTC).timestamp())
    if ttl > 0:
        await redis.set(f"{BLACKLIST_PREFIX}{payload['jti']}", "1", ex=ttl)
```

- [ ] **Step 10: Dependency `get_current_platform_admin`**

Em `apps/api/app/api/deps.py`, adicionar (após `get_tenant_session`):

```python
from app.core.platform_security import decode_platform_token


class PlatformAdminContext(BaseModel):
    admin_id: uuid.UUID
    role: str


async def get_current_platform_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> PlatformAdminContext:
    """Decodifica o JWT do platform_admin — secret separado do de tenant."""
    if credentials is None:
        raise _NAO_AUTENTICADO
    try:
        payload = decode_platform_token(credentials.credentials)
    except jwt.PyJWTError:
        raise _NAO_AUTENTICADO
    if payload.get("type") != "platform_access":
        raise _NAO_AUTENTICADO

    return PlatformAdminContext(admin_id=payload["sub"], role=payload["role"])
```

(o import de `decode_platform_token` vai junto dos outros imports no topo do arquivo, não inline — ajuste a posição.)

- [ ] **Step 11: Rotas**

Criar `apps/api/app/api/v1/platform_admin/__init__.py` (vazio).

Criar `apps/api/app/api/v1/platform_admin/auth.py`:

```python
from fastapi import APIRouter, Depends, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.redis import get_redis
from app.schemas.platform_admin import (
    PlatformAdminLoginRequest,
    PlatformRefreshRequest,
    PlatformTokenPair,
)
from app.services import platform_admin_auth

router = APIRouter(prefix="/platform-admin/auth", tags=["platform-admin"])


@router.post("/login")
async def login(
    body: PlatformAdminLoginRequest,
    session: AsyncSession = Depends(get_session),
) -> PlatformTokenPair:
    access_token, refresh_token = await platform_admin_auth.login(
        body.email, body.password, session
    )
    return PlatformTokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh")
async def refresh(
    body: PlatformRefreshRequest,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> PlatformTokenPair:
    access_token, refresh_token = await platform_admin_auth.refresh(
        body.refresh_token, session, redis
    )
    return PlatformTokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: PlatformRefreshRequest,
    redis: Redis = Depends(get_redis),
) -> None:
    await platform_admin_auth.logout(body.refresh_token, redis)
```

- [ ] **Step 12: Registrar no router principal**

Em `apps/api/app/api/v1/router.py`, adicionar:

```python
from app.api.v1.platform_admin.auth import router as platform_admin_auth_router
```

```python
api_router.include_router(platform_admin_auth_router)
```

- [ ] **Step 13: Script de seed**

Criar `apps/api/scripts/seed_platform_admin.py`:

```python
"""Seed de um platform_admin (back-office da Advoxs — nunca pertence a um tenant).

Uso (dentro de apps/api, com DATABASE_URL no ambiente):

    uv run python scripts/seed_platform_admin.py \
        --name "Falcão" --email falcao@advoxs.com.br --password segredo123
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import PlatformAdmin


async def seed(args: argparse.Namespace) -> None:
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(PlatformAdmin).where(PlatformAdmin.email == args.email)
        )
        if existing is not None:
            print(f"platform_admin {args.email} já existe — nada a criar.")
            return

        session.add(
            PlatformAdmin(
                name=args.name, email=args.email, password_hash=hash_password(args.password)
            )
        )
        await session.commit()
        print(f"platform_admin {args.email} criado.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    asyncio.run(seed(parser.parse_args()))


if __name__ == "__main__":
    main()
```

- [ ] **Step 14: Rodar os testes e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos os testes PASS, ruff limpo.

- [ ] **Step 15: Validar a migração**

Run (ajustar credenciais conforme seu Postgres local):

```bash
DATABASE_URL="postgresql+asyncpg://advoxs:changeme@localhost:5433/advoxs" REDIS_URL="redis://localhost:6379/0" JWT_SECRET="test" uv run alembic upgrade head
```

Expected: aplica a `0004` sem erro (tabela `admin_audit_logs` criada).

- [ ] **Step 16: Commit**

```bash
git add apps/api/alembic/versions/0004_admin_audit_logs.py apps/api/app/models/admin_audit_log.py apps/api/app/models/__init__.py apps/api/app/core/config.py apps/api/app/core/platform_security.py apps/api/app/api/deps.py apps/api/app/schemas/platform_admin.py apps/api/app/services/platform_admin_auth.py apps/api/app/api/v1/platform_admin/__init__.py apps/api/app/api/v1/platform_admin/auth.py apps/api/scripts/seed_platform_admin.py apps/api/app/api/v1/router.py .env.example apps/api/tests/unit/test_platform_admin_auth_routes.py
git commit -m "feat(api): autenticação do platform_admin (login, refresh, logout)"
```

---

### Task 2: `api` — dashboard agregado

**Files:**
- Create: `apps/api/app/schemas/admin_dashboard.py`
- Create: `apps/api/app/services/admin_dashboard.py`
- Create: `apps/api/app/api/v1/platform_admin/dashboard.py`
- Modify: `apps/api/app/api/v1/router.py`
- Test: `apps/api/tests/unit/test_admin_dashboard.py`
- Test: `apps/api/tests/unit/test_admin_dashboard_routes.py`

**Interfaces:**
- Consumes: `get_current_platform_admin`/`PlatformAdminContext` da Task 1.
- Produces: `async def build_dashboard(session) -> AdminDashboardOut` em `app.services.admin_dashboard`; `GET /api/v1/platform-admin/dashboard` → `AdminDashboardOut`.

- [ ] **Step 1: Schemas**

Criar `apps/api/app/schemas/admin_dashboard.py`:

```python
import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class TenantsByStatus(BaseModel):
    active: int
    suspended: int


class NewTenantsPerDay(BaseModel):
    day: date
    count: int


class CreditsSummary(BaseModel):
    sold: int
    consumed: int


class LowBalanceTenant(BaseModel):
    id: uuid.UUID
    name: str
    credit_balance: int


class WhatsappConnectedSummary(BaseModel):
    connected: int
    total: int


class KnowledgeBaseUsageSummary(BaseModel):
    total_files: int
    total_size_bytes: int


class AdminDashboardOut(BaseModel):
    tenants_total: int
    tenants_by_status: TenantsByStatus
    new_tenants_last_30_days: list[NewTenantsPerDay]
    revenue_brl_last_30_days: Decimal
    credits_summary: CreditsSummary
    messages_processed: int
    agent_executions: int
    tokens_consumed: int
    low_balance_tenants: list[LowBalanceTenant]
    whatsapp_connected: WhatsappConnectedSummary
    knowledge_base_usage: KnowledgeBaseUsageSummary
```

- [ ] **Step 2: Escrever o teste que falha**

Criar `apps/api/tests/unit/test_admin_dashboard.py`. **Nota importante**: este teste usa `side_effect` posicional em `session.scalar`/`session.execute` — a ordem das chamadas na lista precisa bater exatamente com a ordem em que `build_dashboard` chama `session.scalar`/`session.execute` no Step 3 abaixo (10 chamadas a `scalar`, 3 a `execute`, na ordem: tenants_total → by_status [execute] → new_tenants [execute] → revenue → sold → consumed_negative → messages_processed → agent_executions → tokens_consumed → low_balance [execute] → whatsapp_connected → kb_files → kb_bytes). Se a ordem no service mudar, a ordem dos `side_effect` abaixo precisa mudar junto.

```python
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.admin_dashboard import build_dashboard

TENANT_ID = uuid.uuid4()


def _execute_result(rows: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


@pytest.fixture
def session():
    return AsyncMock()


class TestBuildDashboard:
    async def test_monta_o_snapshot_com_os_valores_agregados(self, session) -> None:
        session.scalar = AsyncMock(
            side_effect=[
                42,  # tenants_total
                Decimal("350.00"),  # revenue_brl_last_30_days
                5000,  # sold
                -1200,  # consumed_negative
                987,  # messages_processed
                310,  # agent_executions
                45000,  # tokens_consumed
                3,  # whatsapp_connected
                12,  # kb_files
                204800,  # kb_bytes
            ]
        )
        session.execute = AsyncMock(
            side_effect=[
                _execute_result([("active", 40), ("suspended", 2)]),
                _execute_result([(date(2026, 7, 1), 2), (date(2026, 7, 2), 1)]),
                _execute_result([(TENANT_ID, "Escritório Baixo", 10)]),
            ]
        )

        result = await build_dashboard(session)

        assert result.tenants_total == 42
        assert result.tenants_by_status.active == 40
        assert result.tenants_by_status.suspended == 2
        assert len(result.new_tenants_last_30_days) == 2
        assert result.new_tenants_last_30_days[0].count == 2
        assert result.revenue_brl_last_30_days == Decimal("350.00")
        assert result.credits_summary.sold == 5000
        assert result.credits_summary.consumed == 1200  # abs() do valor negativo
        assert result.messages_processed == 987
        assert result.agent_executions == 310
        assert result.tokens_consumed == 45000
        assert result.low_balance_tenants[0].name == "Escritório Baixo"
        assert result.whatsapp_connected.connected == 3
        assert result.whatsapp_connected.total == 42
        assert result.knowledge_base_usage.total_files == 12
        assert result.knowledge_base_usage.total_size_bytes == 204800
```

Run: `cd apps/api && uv run pytest tests/unit/test_admin_dashboard.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'app.services.admin_dashboard'`.

- [ ] **Step 3: Service**

Criar `apps/api/app/services/admin_dashboard.py`:

```python
"""Métricas agregadas do painel de administração — leitura pura de toda a
plataforma, sem filtro por tenant_id."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CreditPackage,
    CreditTransaction,
    KnowledgeBaseFile,
    Message,
    Tenant,
    WhatsAppNumber,
)
from app.schemas.admin_dashboard import (
    AdminDashboardOut,
    CreditsSummary,
    KnowledgeBaseUsageSummary,
    LowBalanceTenant,
    NewTenantsPerDay,
    TenantsByStatus,
    WhatsappConnectedSummary,
)

LOW_BALANCE_LIMIT = 10
PERIOD_DAYS = 30


async def build_dashboard(session: AsyncSession) -> AdminDashboardOut:
    since = datetime.now(UTC) - timedelta(days=PERIOD_DAYS)

    tenants_total = await session.scalar(select(func.count(Tenant.id))) or 0

    by_status_rows = (
        await session.execute(
            select(Tenant.status, func.count(Tenant.id)).group_by(Tenant.status)
        )
    ).all()
    by_status = dict(by_status_rows)
    tenants_by_status = TenantsByStatus(
        active=by_status.get("active", 0), suspended=by_status.get("suspended", 0)
    )

    new_tenants_rows = (
        await session.execute(
            select(func.date(Tenant.created_at), func.count(Tenant.id))
            .where(Tenant.created_at >= since)
            .group_by(func.date(Tenant.created_at))
            .order_by(func.date(Tenant.created_at))
        )
    ).all()
    new_tenants_last_30_days = [
        NewTenantsPerDay(day=day, count=count) for day, count in new_tenants_rows
    ]

    revenue_brl_last_30_days = (
        await session.scalar(
            select(func.coalesce(func.sum(CreditPackage.price_brl), 0))
            .select_from(CreditTransaction)
            .join(CreditPackage, CreditTransaction.credit_package_id == CreditPackage.id)
            .where(CreditTransaction.type == "purchase", CreditTransaction.created_at >= since)
        )
        or Decimal("0")
    )

    sold = (
        await session.scalar(
            select(func.coalesce(func.sum(CreditTransaction.amount_credits), 0)).where(
                CreditTransaction.type == "purchase"
            )
        )
        or 0
    )
    consumed_negative = (
        await session.scalar(
            select(func.coalesce(func.sum(CreditTransaction.amount_credits), 0)).where(
                CreditTransaction.type == "consumption"
            )
        )
        or 0
    )
    credits_summary = CreditsSummary(sold=sold, consumed=abs(consumed_negative))

    messages_processed = await session.scalar(select(func.count(Message.id))) or 0
    agent_executions = (
        await session.scalar(
            select(func.count(Message.id)).where(Message.tokens_used.is_not(None))
        )
        or 0
    )
    tokens_consumed = (
        await session.scalar(select(func.coalesce(func.sum(Message.tokens_used), 0))) or 0
    )

    low_balance_rows = (
        await session.execute(
            select(Tenant.id, Tenant.name, Tenant.credit_balance)
            .order_by(Tenant.credit_balance.asc())
            .limit(LOW_BALANCE_LIMIT)
        )
    ).all()
    low_balance_tenants = [
        LowBalanceTenant(id=id_, name=name, credit_balance=balance)
        for id_, name, balance in low_balance_rows
    ]

    whatsapp_connected = (
        await session.scalar(
            select(func.count(WhatsAppNumber.id)).where(WhatsAppNumber.status == "connected")
        )
        or 0
    )
    whatsapp_summary = WhatsappConnectedSummary(connected=whatsapp_connected, total=tenants_total)

    kb_files = await session.scalar(select(func.count(KnowledgeBaseFile.id))) or 0
    kb_bytes = (
        await session.scalar(select(func.coalesce(func.sum(KnowledgeBaseFile.size_bytes), 0)))
        or 0
    )
    kb_usage = KnowledgeBaseUsageSummary(total_files=kb_files, total_size_bytes=kb_bytes)

    return AdminDashboardOut(
        tenants_total=tenants_total,
        tenants_by_status=tenants_by_status,
        new_tenants_last_30_days=new_tenants_last_30_days,
        revenue_brl_last_30_days=revenue_brl_last_30_days,
        credits_summary=credits_summary,
        messages_processed=messages_processed,
        agent_executions=agent_executions,
        tokens_consumed=tokens_consumed,
        low_balance_tenants=low_balance_tenants,
        whatsapp_connected=whatsapp_summary,
        knowledge_base_usage=kb_usage,
    )
```

- [ ] **Step 4: Rota**

Criar `apps/api/app/api/v1/platform_admin/dashboard.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PlatformAdminContext, get_current_platform_admin
from app.core.db import get_session
from app.schemas.admin_dashboard import AdminDashboardOut
from app.services.admin_dashboard import build_dashboard

router = APIRouter(prefix="/platform-admin/dashboard", tags=["platform-admin"])


@router.get("")
async def get_dashboard(
    admin: PlatformAdminContext = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminDashboardOut:
    return await build_dashboard(session)
```

Criar `apps/api/tests/unit/test_admin_dashboard_routes.py`:

```python
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import app.api.v1.platform_admin.dashboard as dashboard_module
from app.api.deps import get_current_platform_admin, PlatformAdminContext
from app.core.db import get_session
from app.main import app
from app.schemas.admin_dashboard import (
    AdminDashboardOut,
    CreditsSummary,
    KnowledgeBaseUsageSummary,
    TenantsByStatus,
    WhatsappConnectedSummary,
)
import uuid


def _dummy_dashboard() -> AdminDashboardOut:
    return AdminDashboardOut(
        tenants_total=1,
        tenants_by_status=TenantsByStatus(active=1, suspended=0),
        new_tenants_last_30_days=[],
        revenue_brl_last_30_days=0,
        credits_summary=CreditsSummary(sold=0, consumed=0),
        messages_processed=0,
        agent_executions=0,
        tokens_consumed=0,
        low_balance_tenants=[],
        whatsapp_connected=WhatsappConnectedSummary(connected=0, total=1),
        knowledge_base_usage=KnowledgeBaseUsageSummary(total_files=0, total_size_bytes=0),
    )


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/platform-admin/dashboard")
    assert response.status_code == 401


def test_com_token_retorna_o_dashboard(monkeypatch) -> None:
    async def override_admin():
        return PlatformAdminContext(admin_id=uuid.uuid4(), role="superadmin")

    async def override_session():
        yield AsyncMock()

    monkeypatch.setattr(dashboard_module, "build_dashboard", AsyncMock(return_value=_dummy_dashboard()))
    app.dependency_overrides[get_current_platform_admin] = override_admin
    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).get("/api/v1/platform-admin/dashboard")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["tenants_total"] == 1
```

- [ ] **Step 5: Registrar no router principal**

Em `apps/api/app/api/v1/router.py`, adicionar:

```python
from app.api.v1.platform_admin.dashboard import router as platform_admin_dashboard_router
```

```python
api_router.include_router(platform_admin_dashboard_router)
```

- [ ] **Step 6: Rodar os testes e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos PASS, ruff limpo.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/schemas/admin_dashboard.py apps/api/app/services/admin_dashboard.py apps/api/app/api/v1/platform_admin/dashboard.py apps/api/app/api/v1/router.py apps/api/tests/unit/test_admin_dashboard.py apps/api/tests/unit/test_admin_dashboard_routes.py
git commit -m "feat(api): dashboard agregado do painel de administração"
```

---

### Task 3: `api` — lista e detalhe de tenants (com auditoria)

**Files:**
- Create: `apps/api/app/schemas/admin_tenants.py`
- Create: `apps/api/app/services/admin_tenants.py`
- Create: `apps/api/app/api/v1/platform_admin/tenants.py`
- Modify: `apps/api/app/api/v1/router.py`
- Test: `apps/api/tests/unit/test_admin_tenants_service.py`
- Test: `apps/api/tests/unit/test_admin_tenants_routes.py`

**Interfaces:**
- Consumes: `get_current_platform_admin`/`PlatformAdminContext` da Task 1; `AdminAuditLog` model da Task 1.
- Produces: `async def list_tenants(session, limit, offset) -> list[TenantListItemOut]`, `async def get_tenant_detail(session, tenant_id, platform_admin_id) -> TenantDetailOut | None` em `app.services.admin_tenants`; `GET /api/v1/platform-admin/tenants`, `GET /api/v1/platform-admin/tenants/{tenant_id}`.

- [ ] **Step 1: Schemas**

Criar `apps/api/app/schemas/admin_tenants.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TenantListItemOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    credit_balance: int
    created_at: datetime
    whatsapp_connected: bool


class CreditTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    amount_credits: int
    description: str | None
    created_at: datetime


class KnowledgeBaseFileSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    status: str
    uploaded_at: datetime


class TenantDetailOut(BaseModel):
    id: uuid.UUID
    name: str
    email_contato: str
    status: str
    credit_balance: int
    created_at: datetime
    recent_transactions: list[CreditTransactionOut]
    knowledge_base_files: list[KnowledgeBaseFileSummaryOut]
```

- [ ] **Step 2: Escrever os testes que falham**

Criar `apps/api/tests/unit/test_admin_tenants_service.py`:

```python
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.admin_tenants import get_tenant_detail, list_tenants

TENANT_ID = uuid.uuid4()
ADMIN_ID = uuid.uuid4()


def _tenant() -> SimpleNamespace:
    return SimpleNamespace(
        id=TENANT_ID,
        name="Escritório Teste",
        email_contato="a@b.com",
        status="active",
        credit_balance=500,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
    )


@pytest.fixture
def session():
    return AsyncMock()


def _execute_result(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


class TestListTenants:
    async def test_marca_whatsapp_conectado_corretamente(self, session) -> None:
        session.execute.side_effect = [
            _execute_result([_tenant()]),
            _execute_result([TENANT_ID]),  # tenant_ids com whatsapp conectado
        ]

        result = await list_tenants(session, limit=50, offset=0)

        assert len(result) == 1
        assert result[0].whatsapp_connected is True


class TestGetTenantDetail:
    async def test_tenant_inexistente_retorna_none(self, session) -> None:
        session.get.return_value = None

        result = await get_tenant_detail(session, TENANT_ID, ADMIN_ID)

        assert result is None
        session.add.assert_not_called()

    async def test_tenant_existente_grava_auditoria_e_retorna_detalhe(self, session) -> None:
        session.get.return_value = _tenant()
        session.execute.side_effect = [_execute_result([]), _execute_result([])]

        result = await get_tenant_detail(session, TENANT_ID, ADMIN_ID)

        assert result is not None
        assert result.name == "Escritório Teste"
        session.add.assert_called_once()
        audit_log = session.add.call_args.args[0]
        assert audit_log.platform_admin_id == ADMIN_ID
        assert audit_log.tenant_id == TENANT_ID
        session.commit.assert_awaited_once()
```

Criar `apps/api/tests/unit/test_admin_tenants_routes.py`:

```python
import uuid
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import app.api.v1.platform_admin.tenants as tenants_module
from app.api.deps import PlatformAdminContext, get_current_platform_admin
from app.core.db import get_session
from app.main import app

TENANT_ID = uuid.uuid4()


def _client(monkeypatch):
    async def override_admin():
        return PlatformAdminContext(admin_id=uuid.uuid4(), role="superadmin")

    async def override_session():
        yield AsyncMock()

    app.dependency_overrides[get_current_platform_admin] = override_admin
    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def test_lista_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/platform-admin/tenants")
    assert response.status_code == 401


def test_detalhe_tenant_nao_encontrado_retorna_404(monkeypatch) -> None:
    monkeypatch.setattr(tenants_module, "get_tenant_detail", AsyncMock(return_value=None))
    client = _client(monkeypatch)
    try:
        response = client.get(f"/api/v1/platform-admin/tenants/{TENANT_ID}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_admin_tenants_service.py tests/unit/test_admin_tenants_routes.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'app.services.admin_tenants'`.

- [ ] **Step 4: Service**

Criar `apps/api/app/services/admin_tenants.py`:

```python
"""Listagem e detalhe de tenants para o painel de administração.

Leitura de um tenant específico (get_tenant_detail) é auditada em
AdminAuditLog — implementa a exigência do CLAUDE.md.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AdminAuditLog,
    CreditTransaction,
    KnowledgeBaseFile,
    Tenant,
    WhatsAppNumber,
)
from app.schemas.admin_tenants import (
    CreditTransactionOut,
    KnowledgeBaseFileSummaryOut,
    TenantDetailOut,
    TenantListItemOut,
)

RECENT_TRANSACTIONS_LIMIT = 20


async def list_tenants(
    session: AsyncSession, limit: int, offset: int
) -> list[TenantListItemOut]:
    tenants = (
        await session.execute(
            select(Tenant).order_by(Tenant.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()

    connected_ids = set(
        (
            await session.execute(
                select(WhatsAppNumber.tenant_id).where(WhatsAppNumber.status == "connected")
            )
        )
        .scalars()
        .all()
    )

    return [
        TenantListItemOut(
            id=t.id,
            name=t.name,
            status=t.status,
            credit_balance=t.credit_balance,
            created_at=t.created_at,
            whatsapp_connected=t.id in connected_ids,
        )
        for t in tenants
    ]


async def get_tenant_detail(
    session: AsyncSession, tenant_id: uuid.UUID, platform_admin_id: uuid.UUID
) -> TenantDetailOut | None:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        return None

    transactions = (
        await session.execute(
            select(CreditTransaction)
            .where(CreditTransaction.tenant_id == tenant_id)
            .order_by(CreditTransaction.created_at.desc())
            .limit(RECENT_TRANSACTIONS_LIMIT)
        )
    ).scalars().all()

    files = (
        await session.execute(
            select(KnowledgeBaseFile)
            .where(KnowledgeBaseFile.tenant_id == tenant_id)
            .order_by(KnowledgeBaseFile.uploaded_at.desc())
        )
    ).scalars().all()

    session.add(AdminAuditLog(platform_admin_id=platform_admin_id, tenant_id=tenant_id))
    await session.commit()

    return TenantDetailOut(
        id=tenant.id,
        name=tenant.name,
        email_contato=tenant.email_contato,
        status=tenant.status,
        credit_balance=tenant.credit_balance,
        created_at=tenant.created_at,
        recent_transactions=[CreditTransactionOut.model_validate(t) for t in transactions],
        knowledge_base_files=[KnowledgeBaseFileSummaryOut.model_validate(f) for f in files],
    )
```

- [ ] **Step 5: Rotas**

Criar `apps/api/app/api/v1/platform_admin/tenants.py`:

```python
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PlatformAdminContext, get_current_platform_admin
from app.core.db import get_session
from app.schemas.admin_tenants import TenantDetailOut, TenantListItemOut
from app.services.admin_tenants import get_tenant_detail, list_tenants

router = APIRouter(prefix="/platform-admin/tenants", tags=["platform-admin"])


@router.get("")
async def list_tenants_route(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: PlatformAdminContext = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> list[TenantListItemOut]:
    return await list_tenants(session, limit, offset)


@router.get("/{tenant_id}")
async def get_tenant_route(
    tenant_id: uuid.UUID,
    admin: PlatformAdminContext = Depends(get_current_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> TenantDetailOut:
    detail = await get_tenant_detail(session, tenant_id, admin.admin_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant não encontrado")
    return detail
```

- [ ] **Step 6: Registrar no router principal**

Em `apps/api/app/api/v1/router.py`:

```python
from app.api.v1.platform_admin.tenants import router as platform_admin_tenants_router
```

```python
api_router.include_router(platform_admin_tenants_router)
```

- [ ] **Step 7: Rodar os testes e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos PASS, ruff limpo.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/schemas/admin_tenants.py apps/api/app/services/admin_tenants.py apps/api/app/api/v1/platform_admin/tenants.py apps/api/app/api/v1/router.py apps/api/tests/unit/test_admin_tenants_service.py apps/api/tests/unit/test_admin_tenants_routes.py
git commit -m "feat(api): lista e detalhe de tenants no painel de administração (com auditoria)"
```

---

### Task 4: `web` — auth do admin (proxy, middleware, login)

**Files:**
- Create: `apps/web/src/lib/admin-auth.ts`
- Create: `apps/web/src/lib/admin-backend.ts`
- Create: `apps/web/src/lib/admin-client-api.ts`
- Create: `apps/web/src/app/api/admin-backend/[...path]/route.ts`
- Create: `apps/web/src/app/admin/actions.ts`
- Create: `apps/web/src/components/AdminLoginForm.tsx`
- Create: `apps/web/src/app/admin/login/page.tsx`
- Modify: `apps/web/src/middleware.ts`
- Test: `apps/web/__tests__/admin-backend.test.ts`
- Test: `apps/web/__tests__/admin-login-actions.test.ts`

**Interfaces:**
- Produces: `PLATFORM_ACCESS_TOKEN_COOKIE`, `PLATFORM_REFRESH_TOKEN_COOKIE`, `setPlatformAuthCookies`, `clearPlatformAuthCookies` em `@/lib/admin-auth`; `isAdminAllowedPath` em `@/lib/admin-backend`; `adminBackendFetch` em `@/lib/admin-client-api`; `adminLogin`/`adminLogout`/`AdminLoginState` em `@/app/admin/actions`.

- [ ] **Step 1: Cookies do admin**

Criar `apps/web/src/lib/admin-auth.ts`:

```ts
/** Cookies httpOnly com os tokens do platform_admin — isolados dos cookies de tenant. */

export const PLATFORM_ACCESS_TOKEN_COOKIE = "platform_access_token";
export const PLATFORM_REFRESH_TOKEN_COOKIE = "platform_refresh_token";

const ACCESS_MAX_AGE_SECONDS = 15 * 60;
const REFRESH_MAX_AGE_SECONDS = 30 * 24 * 60 * 60;

interface CookieSetter {
  set(name: string, value: string, options: Record<string, unknown>): void;
  delete(name: string): void;
}

function baseOptions() {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
  };
}

export function setPlatformAuthCookies(
  store: CookieSetter,
  tokens: { access_token: string; refresh_token: string },
): void {
  store.set(PLATFORM_ACCESS_TOKEN_COOKIE, tokens.access_token, {
    ...baseOptions(),
    maxAge: ACCESS_MAX_AGE_SECONDS,
  });
  store.set(PLATFORM_REFRESH_TOKEN_COOKIE, tokens.refresh_token, {
    ...baseOptions(),
    maxAge: REFRESH_MAX_AGE_SECONDS,
  });
}

export function clearPlatformAuthCookies(store: CookieSetter): void {
  store.delete(PLATFORM_ACCESS_TOKEN_COOKIE);
  store.delete(PLATFORM_REFRESH_TOKEN_COOKIE);
}
```

- [ ] **Step 2: Teste que falha (allowlist do proxy do admin)**

Criar `apps/web/__tests__/admin-backend.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { isAdminAllowedPath } from "@/lib/admin-backend";

describe("isAdminAllowedPath", () => {
  it("permite rotas de platform-admin", () => {
    expect(isAdminAllowedPath(["platform-admin", "dashboard"])).toBe(true);
    expect(isAdminAllowedPath(["platform-admin", "tenants", "abc"])).toBe(true);
  });

  it("bloqueia rotas de tenant e caminho vazio", () => {
    expect(isAdminAllowedPath(["conversations"])).toBe(false);
    expect(isAdminAllowedPath(["knowledge-base", "files"])).toBe(false);
    expect(isAdminAllowedPath([])).toBe(false);
  });
});
```

Run: `cd apps/web && npx --yes pnpm@9 test -- admin-backend`
Expected: FAIL — `@/lib/admin-backend` não existe.

- [ ] **Step 3: Allowlist**

Criar `apps/web/src/lib/admin-backend.ts`:

```ts
const ALLOWED_PREFIXES = ["platform-admin"];

/** Só rotas do painel de admin passam por este proxy — nunca as de tenant. */
export function isAdminAllowedPath(path: string[]): boolean {
  const [first] = path;
  return first !== undefined && ALLOWED_PREFIXES.includes(first);
}
```

Run: `cd apps/web && npx --yes pnpm@9 test -- admin-backend` → PASS.

- [ ] **Step 4: Proxy dedicado**

Criar `apps/web/src/app/api/admin-backend/[...path]/route.ts`:

```ts
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import {
  PLATFORM_ACCESS_TOKEN_COOKIE,
  PLATFORM_REFRESH_TOKEN_COOKIE,
  clearPlatformAuthCookies,
  setPlatformAuthCookies,
} from "@/lib/admin-auth";
import { isAdminAllowedPath } from "@/lib/admin-backend";
import { API_URL } from "@/lib/backend";

/**
 * Proxy autenticado do painel de admin — nunca reaproveita o proxy dos
 * tenants (/api/backend/*): cookies, endpoint de refresh e allowlist
 * próprios, pra sessão de admin nunca se confundir com sessão de tenant.
 */
async function handle(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const { path } = await params;
  if (!isAdminAllowedPath(path)) {
    return NextResponse.json({ detail: "Rota não permitida" }, { status: 404 });
  }

  const store = await cookies();
  const url = `${API_URL}/api/v1/${path.join("/")}${request.nextUrl.search}`;
  const hasBody = request.method !== "GET" && request.method !== "DELETE";
  const contentType = request.headers.get("content-type");
  const body = hasBody ? await request.arrayBuffer() : undefined;

  const forward = (token: string | undefined) =>
    fetch(url, {
      method: request.method,
      headers: {
        ...(hasBody && contentType ? { "content-type": contentType } : {}),
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body,
      cache: "no-store",
    });

  let response = await forward(store.get(PLATFORM_ACCESS_TOKEN_COOKIE)?.value);

  if (response.status === 401) {
    const newAccessToken = await refreshSession(store);
    if (newAccessToken === null) {
      return NextResponse.json({ detail: "Sessão expirada" }, { status: 401 });
    }
    response = await forward(newAccessToken);
  }

  const payload = await response.text();
  return new NextResponse(payload, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
    },
  });
}

async function refreshSession(
  store: Awaited<ReturnType<typeof cookies>>,
): Promise<string | null> {
  const refreshToken = store.get(PLATFORM_REFRESH_TOKEN_COOKIE)?.value;
  if (!refreshToken) {
    return null;
  }

  const response = await fetch(`${API_URL}/api/v1/platform-admin/auth/refresh`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
    cache: "no-store",
  });

  if (!response.ok) {
    clearPlatformAuthCookies(store);
    return null;
  }

  const tokens = await response.json();
  setPlatformAuthCookies(store, tokens);
  return tokens.access_token;
}

export { handle as GET, handle as POST, handle as PATCH, handle as DELETE };
```

- [ ] **Step 5: Client fetch do admin**

Criar `apps/web/src/lib/admin-client-api.ts`:

```ts
"use client";

/** Fetch do browser via proxy autenticado do admin; sessão expirada volta pro /admin/login. */
export async function adminBackendFetch(path: string, init?: RequestInit): Promise<Response> {
  const isFormData = init?.body instanceof FormData;
  const response = await fetch(`/api/admin-backend/${path}`, {
    ...init,
    headers: {
      ...(isFormData ? {} : { "content-type": "application/json" }),
      ...init?.headers,
    },
  });
  if (response.status === 401) {
    window.location.href = "/admin/login";
    throw new Error("Sessão expirada");
  }
  return response;
}
```

- [ ] **Step 6: Teste que falha (Server Action de login)**

Criar `apps/web/__tests__/admin-login-actions.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  redirect: vi.fn(),
}));

import { redirect } from "next/navigation";

import { adminLogin } from "@/app/admin/actions";

const mockedRedirect = redirect as ReturnType<typeof vi.fn>;
const mockedFetch = vi.fn();

beforeEach(() => {
  mockedRedirect.mockReset();
  mockedFetch.mockReset();
  vi.stubGlobal("fetch", mockedFetch);
});

function formData(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) data.append(key, value);
  return data;
}

describe("adminLogin action", () => {
  it("redireciona para /admin em caso de sucesso", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ access_token: "a", refresh_token: "b" }),
    });

    await adminLogin({ error: null }, formData({ email: "a@b.com", password: "senha123" }));

    expect(mockedRedirect).toHaveBeenCalledWith("/admin");
  });

  it("retorna erro claro em credenciais inválidas (401)", async () => {
    mockedFetch.mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });

    const result = await adminLogin(
      { error: null },
      formData({ email: "a@b.com", password: "errada" }),
    );

    expect(result.error).toBe("E-mail ou senha incorretos.");
    expect(mockedRedirect).not.toHaveBeenCalled();
  });

  it("trata falha de rede", async () => {
    mockedFetch.mockRejectedValue(new Error("network down"));

    const result = await adminLogin({ error: null }, formData({ email: "a@b.com", password: "x" }));

    expect(result.error).toBe("Não foi possível conectar ao servidor. Tente novamente.");
  });
});
```

Run: `cd apps/web && npx --yes pnpm@9 test -- admin-login-actions`
Expected: FAIL — `@/app/admin/actions` não existe.

- [ ] **Step 7: Server Actions de login/logout**

Criar `apps/web/src/app/admin/actions.ts`:

```ts
"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { clearPlatformAuthCookies, PLATFORM_REFRESH_TOKEN_COOKIE, setPlatformAuthCookies } from "@/lib/admin-auth";
import { API_URL } from "@/lib/backend";

export interface AdminLoginState {
  error: string | null;
}

export async function adminLogin(
  _prev: AdminLoginState,
  formData: FormData,
): Promise<AdminLoginState> {
  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");

  let tokens: { access_token: string; refresh_token: string };
  try {
    const response = await fetch(`${API_URL}/api/v1/platform-admin/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
    });

    if (response.status === 401) {
      return { error: "E-mail ou senha incorretos." };
    }
    if (!response.ok) {
      return { error: "Não foi possível entrar agora. Tente novamente." };
    }
    tokens = await response.json();
  } catch {
    return { error: "Não foi possível conectar ao servidor. Tente novamente." };
  }

  setPlatformAuthCookies(await cookies(), tokens);
  redirect("/admin");
}

export async function adminLogout(): Promise<void> {
  const store = await cookies();
  const refreshToken = store.get(PLATFORM_REFRESH_TOKEN_COOKIE)?.value;

  if (refreshToken) {
    // Revogação no servidor é melhor esforço — a sessão local sempre encerra.
    try {
      await fetch(`${API_URL}/api/v1/platform-admin/auth/logout`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
        cache: "no-store",
      });
    } catch {
      // segue o fluxo local
    }
  }

  clearPlatformAuthCookies(store);
  redirect("/admin/login");
}
```

- [ ] **Step 8: Rodar o teste da action**

Run: `cd apps/web && npx --yes pnpm@9 test -- admin-login-actions`
Expected: PASS (3/3).

- [ ] **Step 9: Formulário e página de login**

Criar `apps/web/src/components/AdminLoginForm.tsx`:

```tsx
"use client";

import { useActionState } from "react";

import { adminLogin, type AdminLoginState } from "@/app/admin/actions";

const initialState: AdminLoginState = { error: null };

export function AdminLoginForm() {
  const [state, formAction, pending] = useActionState(adminLogin, initialState);

  return (
    <form action={formAction} className="flex flex-col gap-5">
      <div className="flex flex-col gap-1.5">
        <label htmlFor="email" className="text-sm font-medium">
          E-mail
        </label>
        <input
          id="email"
          name="email"
          type="email"
          required
          autoComplete="email"
          className="rounded-sm border border-line bg-surface px-3 py-2.5 text-sm placeholder:text-muted"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="password" className="text-sm font-medium">
          Senha
        </label>
        <input
          id="password"
          name="password"
          type="password"
          required
          autoComplete="current-password"
          className="rounded-sm border border-line bg-surface px-3 py-2.5 text-sm"
        />
      </div>

      {state.error ? (
        <p role="alert" className="border-l-2 border-danger pl-3 text-sm text-danger">
          {state.error}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={pending}
        className="mt-1 rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink disabled:opacity-60"
      >
        {pending ? "Entrando…" : "Entrar"}
      </button>
    </form>
  );
}
```

Criar `apps/web/src/app/admin/login/page.tsx`:

```tsx
import { AdminLoginForm } from "@/components/AdminLoginForm";

export default function AdminLoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-muted">
          Back-office Advoxs
        </p>
        <h1 className="mt-2 font-display text-5xl font-semibold text-ink">
          Admin<span className="text-accent">.</span>
        </h1>

        <hr className="my-8 border-line" />

        <AdminLoginForm />
      </div>
    </main>
  );
}
```

- [ ] **Step 10: Middleware — bloco isolado pra `/admin`**

Em `apps/web/src/middleware.ts`, adicionar o import e um bloco novo, **antes** da lógica de tenant já existente (early return, nunca cai no bloco de tenant):

```ts
import { PLATFORM_ACCESS_TOKEN_COOKIE, PLATFORM_REFRESH_TOKEN_COOKIE } from "@/lib/admin-auth";
```

```ts
  if (pathname.startsWith("/admin")) {
    const hasPlatformSession =
      request.cookies.has(PLATFORM_ACCESS_TOKEN_COOKIE) ||
      request.cookies.has(PLATFORM_REFRESH_TOKEN_COOKIE);

    if (pathname === "/admin/login") {
      if (hasPlatformSession) {
        return NextResponse.redirect(new URL("/admin", request.url));
      }
      return NextResponse.next();
    }

    if (!hasPlatformSession) {
      return NextResponse.redirect(new URL("/admin/login", request.url));
    }
    return NextResponse.next();
  }
```

Esse bloco entra logo depois de `const { pathname } = request.nextUrl;` e antes do `if (pathname === "/")` — arquivo final:

```ts
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { PLATFORM_ACCESS_TOKEN_COOKIE, PLATFORM_REFRESH_TOKEN_COOKIE } from "@/lib/admin-auth";
import { ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE } from "@/lib/auth";

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (pathname.startsWith("/admin")) {
    const hasPlatformSession =
      request.cookies.has(PLATFORM_ACCESS_TOKEN_COOKIE) ||
      request.cookies.has(PLATFORM_REFRESH_TOKEN_COOKIE);

    if (pathname === "/admin/login") {
      if (hasPlatformSession) {
        return NextResponse.redirect(new URL("/admin", request.url));
      }
      return NextResponse.next();
    }

    if (!hasPlatformSession) {
      return NextResponse.redirect(new URL("/admin/login", request.url));
    }
    return NextResponse.next();
  }

  const hasSession =
    request.cookies.has(ACCESS_TOKEN_COOKIE) || request.cookies.has(REFRESH_TOKEN_COOKIE);

  if (pathname === "/") {
    if (hasSession) {
      return NextResponse.redirect(new URL("/conversas", request.url));
    }
    return NextResponse.next();
  }

  if (pathname === "/login" && hasSession) {
    return NextResponse.redirect(new URL("/conversas", request.url));
  }

  if (pathname !== "/login" && !hasSession) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/",
    "/login",
    "/conversas/:path*",
    "/base-de-conhecimento/:path*",
    "/configuracoes/:path*",
    "/admin/:path*",
  ],
};
```

(`/admin/:path*` no `matcher` cobre `/admin` sozinho também — mesmo comportamento de `:path*` já usado pra `/conversas/:path*` cobrir `/conversas` sozinho.)

- [ ] **Step 11: Rodar os testes, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde. `/admin/login` deve aparecer nas rotas geradas pelo build.

- [ ] **Step 12: Commit**

```bash
git add apps/web/src/lib/admin-auth.ts apps/web/src/lib/admin-backend.ts apps/web/src/lib/admin-client-api.ts apps/web/src/app/api/admin-backend apps/web/src/app/admin/actions.ts apps/web/src/components/AdminLoginForm.tsx apps/web/src/app/admin/login apps/web/src/middleware.ts apps/web/__tests__/admin-backend.test.ts apps/web/__tests__/admin-login-actions.test.ts
git commit -m "feat(web): autenticação isolada do painel de administração (/admin)"
```

---

### Task 5: `web` — dashboard (`/admin`)

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Create: `apps/web/src/components/StatTile.tsx`
- Create: `apps/web/src/components/NewTenantsChart.tsx`
- Create: `apps/web/src/components/AdminDashboardPanel.tsx`
- Create: `apps/web/src/app/admin/page.tsx`
- Test: `apps/web/__tests__/AdminDashboardPanel.test.tsx`

**Interfaces:**
- Consumes: `adminBackendFetch` da Task 4; `GET platform-admin/dashboard` da Task 2 (mesma forma de `AdminDashboardOut`).
- Produces: tipo `AdminDashboard` em `@/lib/types`; componentes `StatTile`, `NewTenantsChart`, `AdminDashboardPanel`.

**Nota de design (dataviz)**: a maioria das métricas é um número isolado — usar stat tiles, não gráfico, é a escolha certa pra "magnitude, sem comparação ao longo do tempo" (só "novos escritórios por dia" é série temporal de verdade). O gráfico usa uma única série (sem legenda necessária — o título já identifica), cor única reaproveitada dos tokens já existentes do app (`--accent`/`--accent-soft`), traço fino de 2px, ponta arredondada, hover com hit-target maior que o ponto visível. "Ativos"/"Suspensos" usam os tokens de status já estabelecidos no app (`accent`=bom, `danger`=crítico) com label — nunca cor isolada.

- [ ] **Step 1: Tipo `AdminDashboard`**

Em `apps/web/src/lib/types.ts`, adicionar ao final:

```ts
export interface AdminDashboard {
  tenants_total: number;
  tenants_by_status: { active: number; suspended: number };
  new_tenants_last_30_days: { day: string; count: number }[];
  revenue_brl_last_30_days: number;
  credits_summary: { sold: number; consumed: number };
  messages_processed: number;
  agent_executions: number;
  tokens_consumed: number;
  low_balance_tenants: { id: string; name: string; credit_balance: number }[];
  whatsapp_connected: { connected: number; total: number };
  knowledge_base_usage: { total_files: number; total_size_bytes: number };
}
```

- [ ] **Step 2: `StatTile`**

Criar `apps/web/src/components/StatTile.tsx`:

```tsx
type StatTileTone = "neutral" | "good" | "warning" | "critical";

const TONE_CLASS: Record<StatTileTone, string> = {
  neutral: "text-ink",
  good: "text-accent",
  warning: "text-brass",
  critical: "text-danger",
};

export function StatTile({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: StatTileTone;
}) {
  return (
    <div className="rounded-sm border border-line bg-surface p-5">
      <p className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted">{label}</p>
      <p className={`mt-2 font-display text-3xl font-semibold ${TONE_CLASS[tone]}`}>{value}</p>
    </div>
  );
}
```

- [ ] **Step 3: `NewTenantsChart`**

Criar `apps/web/src/components/NewTenantsChart.tsx`:

```tsx
"use client";

import { useState } from "react";

type DataPoint = { day: string; count: number };

const WIDTH = 600;
const HEIGHT = 160;
const PADDING = 24;

export function NewTenantsChart({ data }: { data: DataPoint[] }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  if (data.length === 0) {
    return <p className="text-sm text-muted">Sem novos escritórios nos últimos 30 dias.</p>;
  }

  const maxCount = Math.max(...data.map((d) => d.count), 1);
  const stepX = (WIDTH - PADDING * 2) / Math.max(data.length - 1, 1);

  const points = data.map((d, i) => ({
    x: PADDING + i * stepX,
    y: HEIGHT - PADDING - (d.count / maxCount) * (HEIGHT - PADDING * 2),
    day: d.day,
    count: d.count,
  }));

  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");
  const areaPath = `${linePath} L${points[points.length - 1].x},${HEIGHT - PADDING} L${points[0].x},${HEIGHT - PADDING} Z`;

  const hovered = hoverIndex !== null ? points[hoverIndex] : null;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full"
        onMouseLeave={() => setHoverIndex(null)}
      >
        <path d={areaPath} fill="var(--accent-soft)" />
        <path
          d={linePath}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={2}
          strokeLinecap="round"
        />
        {points.map((p, i) => (
          <rect
            key={p.day}
            x={p.x - stepX / 2}
            y={0}
            width={stepX}
            height={HEIGHT}
            fill="transparent"
            onMouseEnter={() => setHoverIndex(i)}
          />
        ))}
        {hovered && (
          <circle
            cx={hovered.x}
            cy={hovered.y}
            r={4}
            fill="var(--accent)"
            stroke="var(--surface)"
            strokeWidth={2}
          />
        )}
      </svg>
      {hovered && (
        <div
          className="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded-sm border border-line bg-ground px-2 py-1 text-xs text-ink shadow-sm"
          style={{ left: `${(hovered.x / WIDTH) * 100}%`, top: `${(hovered.y / HEIGHT) * 100}%` }}
        >
          {hovered.day}: {hovered.count}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Teste que falha (painel do dashboard)**

Criar `apps/web/__tests__/AdminDashboardPanel.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminDashboardPanel } from "@/components/AdminDashboardPanel";
import { adminBackendFetch } from "@/lib/admin-client-api";

vi.mock("@/lib/admin-client-api", () => ({
  adminBackendFetch: vi.fn(),
}));

const mockedFetch = adminBackendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

const DASHBOARD = {
  tenants_total: 12,
  tenants_by_status: { active: 10, suspended: 2 },
  new_tenants_last_30_days: [{ day: "2026-07-01", count: 3 }],
  revenue_brl_last_30_days: 1500.5,
  credits_summary: { sold: 20000, consumed: 8000 },
  messages_processed: 500,
  agent_executions: 120,
  tokens_consumed: 90000,
  low_balance_tenants: [{ id: "t1", name: "Escritório Baixo", credit_balance: 5 }],
  whatsapp_connected: { connected: 8, total: 12 },
  knowledge_base_usage: { total_files: 30, total_size_bytes: 1048576 },
};

describe("AdminDashboardPanel", () => {
  it("renderiza as métricas a partir do dashboard carregado", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => DASHBOARD });

    render(<AdminDashboardPanel />);

    await waitFor(() => expect(screen.getByText("12")).toBeInTheDocument());
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("Escritório Baixo")).toBeInTheDocument();
    expect(screen.getByText("8 / 12")).toBeInTheDocument();
  });

  it("mostra mensagem de erro quando o dashboard falha ao carregar", async () => {
    mockedFetch.mockResolvedValue({ ok: false });

    render(<AdminDashboardPanel />);

    await waitFor(() =>
      expect(screen.getByText("Não foi possível carregar o dashboard.")).toBeInTheDocument(),
    );
  });
});
```

Run: `cd apps/web && npx --yes pnpm@9 test -- AdminDashboardPanel`
Expected: FAIL — componente não existe.

- [ ] **Step 5: `AdminDashboardPanel`**

Criar `apps/web/src/components/AdminDashboardPanel.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { adminBackendFetch } from "@/lib/admin-client-api";
import type { AdminDashboard } from "@/lib/types";

import { NewTenantsChart } from "./NewTenantsChart";
import { StatTile } from "./StatTile";

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

export function AdminDashboardPanel() {
  const [data, setData] = useState<AdminDashboard | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const response = await adminBackendFetch("platform-admin/dashboard");
        if (response.ok) {
          setData(await response.json());
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, []);

  if (!loaded) {
    return <p className="p-8 text-sm text-muted">Carregando...</p>;
  }
  if (!data) {
    return <p className="p-8 text-sm text-danger">Não foi possível carregar o dashboard.</p>;
  }

  return (
    <div className="flex flex-col gap-8 p-8">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatTile label="Escritórios" value={String(data.tenants_total)} />
        <StatTile label="Ativos" value={String(data.tenants_by_status.active)} tone="good" />
        <StatTile label="Suspensos" value={String(data.tenants_by_status.suspended)} tone="critical" />
        <StatTile
          label="WhatsApp conectado"
          value={`${data.whatsapp_connected.connected} / ${data.whatsapp_connected.total}`}
        />
        <StatTile
          label="Receita (30 dias)"
          value={`R$ ${Number(data.revenue_brl_last_30_days).toFixed(2)}`}
        />
        <StatTile label="Créditos vendidos" value={String(data.credits_summary.sold)} />
        <StatTile label="Créditos consumidos" value={String(data.credits_summary.consumed)} />
        <StatTile label="Mensagens processadas" value={String(data.messages_processed)} />
        <StatTile label="Execuções de agente" value={String(data.agent_executions)} />
        <StatTile label="Tokens consumidos" value={String(data.tokens_consumed)} />
        <StatTile label="Arquivos de KB" value={String(data.knowledge_base_usage.total_files)} />
        <StatTile
          label="Storage de KB"
          value={formatBytes(data.knowledge_base_usage.total_size_bytes)}
        />
      </div>

      <div>
        <h2 className="font-display text-lg font-semibold text-ink">
          Novos escritórios (30 dias)
        </h2>
        <div className="mt-3 rounded-sm border border-line bg-surface p-4">
          <NewTenantsChart data={data.new_tenants_last_30_days} />
        </div>
      </div>

      <div>
        <h2 className="font-display text-lg font-semibold text-ink">Menor saldo de créditos</h2>
        <ul className="mt-3 divide-y divide-line rounded-sm border border-line bg-surface">
          {data.low_balance_tenants.map((t) => (
            <li key={t.id} className="flex items-center justify-between px-4 py-3 text-sm">
              <a
                href={`/admin/tenants/${t.id}`}
                className="text-ink hover:text-accent hover:underline"
              >
                {t.name}
              </a>
              <span className="font-mono text-muted">{t.credit_balance} créditos</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Página**

Criar `apps/web/src/app/admin/page.tsx`:

```tsx
import Link from "next/link";

import { AdminDashboardPanel } from "@/components/AdminDashboardPanel";

import { adminLogout } from "./actions";

export default function AdminDashboardPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <nav className="flex w-14 shrink-0 flex-col items-center justify-between bg-ink py-5">
        <div className="flex flex-col items-center gap-6">
          <span className="font-display text-2xl font-semibold text-ground" aria-label="Admin">
            A.
          </span>
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground [writing-mode:vertical-rl]">
            Dashboard
          </span>
          <Link
            href="/admin/tenants"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Tenants
          </Link>
        </div>
        <form action={adminLogout}>
          <button
            type="submit"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Sair
          </button>
        </form>
      </nav>
      <main className="flex-1 overflow-y-auto bg-ground">
        <AdminDashboardPanel />
      </main>
    </div>
  );
}
```

- [ ] **Step 7: Rodar os testes, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/components/StatTile.tsx apps/web/src/components/NewTenantsChart.tsx apps/web/src/components/AdminDashboardPanel.tsx apps/web/src/app/admin/page.tsx apps/web/__tests__/AdminDashboardPanel.test.tsx
git commit -m "feat(web): dashboard do painel de administração"
```

---

### Task 6: `web` — lista e detalhe de tenants (`/admin/tenants`)

**Files:**
- Create: `apps/web/src/components/AdminTenantsList.tsx`
- Create: `apps/web/src/app/admin/tenants/page.tsx`
- Create: `apps/web/src/components/AdminTenantDetail.tsx`
- Create: `apps/web/src/app/admin/tenants/[id]/page.tsx`
- Test: `apps/web/__tests__/AdminTenantsList.test.tsx`
- Test: `apps/web/__tests__/AdminTenantDetail.test.tsx`

**Interfaces:**
- Consumes: `adminBackendFetch` da Task 4; `GET platform-admin/tenants` e `GET platform-admin/tenants/{id}` da Task 3; `adminLogout` da Task 4.

- [ ] **Step 1: Teste que falha (lista)**

Criar `apps/web/__tests__/AdminTenantsList.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminTenantsList } from "@/components/AdminTenantsList";
import { adminBackendFetch } from "@/lib/admin-client-api";

vi.mock("@/lib/admin-client-api", () => ({
  adminBackendFetch: vi.fn(),
}));

const mockedFetch = adminBackendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("AdminTenantsList", () => {
  it("lista os tenants com status e WhatsApp", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          id: "t1",
          name: "Escritório A",
          status: "active",
          credit_balance: 500,
          created_at: "2026-07-01T12:00:00Z",
          whatsapp_connected: true,
        },
        {
          id: "t2",
          name: "Escritório B",
          status: "suspended",
          credit_balance: 0,
          created_at: "2026-06-01T12:00:00Z",
          whatsapp_connected: false,
        },
      ],
    });

    render(<AdminTenantsList />);

    await waitFor(() => expect(screen.getByText("Escritório A")).toBeInTheDocument());
    expect(screen.getByText("Escritório B")).toBeInTheDocument();
    expect(screen.getByText("ativo")).toBeInTheDocument();
    expect(screen.getByText("suspenso")).toBeInTheDocument();
  });
});
```

Run: `cd apps/web && npx --yes pnpm@9 test -- AdminTenantsList`
Expected: FAIL — componente não existe.

- [ ] **Step 2: `AdminTenantsList`**

Criar `apps/web/src/components/AdminTenantsList.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { adminBackendFetch } from "@/lib/admin-client-api";

type TenantListItem = {
  id: string;
  name: string;
  status: "active" | "suspended";
  credit_balance: number;
  created_at: string;
  whatsapp_connected: boolean;
};

const STATUS_LABEL: Record<TenantListItem["status"], string> = {
  active: "ativo",
  suspended: "suspenso",
};

const STATUS_CLASS: Record<TenantListItem["status"], string> = {
  active: "bg-accent-soft text-accent",
  suspended: "bg-danger/10 text-danger",
};

export function AdminTenantsList() {
  const [tenants, setTenants] = useState<TenantListItem[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const response = await adminBackendFetch("platform-admin/tenants");
        if (response.ok) {
          setTenants(await response.json());
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, []);

  if (!loaded) {
    return <p className="p-8 text-sm text-muted">Carregando...</p>;
  }

  return (
    <div className="p-8">
      <h1 className="font-display text-xl font-semibold text-ink">Escritórios</h1>
      <table className="mt-6 w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line text-xs uppercase tracking-[0.1em] text-muted">
            <th className="py-2">Nome</th>
            <th className="py-2">Status</th>
            <th className="py-2">Créditos</th>
            <th className="py-2">WhatsApp</th>
            <th className="py-2">Criado em</th>
          </tr>
        </thead>
        <tbody>
          {tenants.map((t) => (
            <tr key={t.id} className="border-b border-line">
              <td className="py-3">
                <a
                  href={`/admin/tenants/${t.id}`}
                  className="text-ink hover:text-accent hover:underline"
                >
                  {t.name}
                </a>
              </td>
              <td className="py-3">
                <span
                  className={`rounded-full px-3 py-1 font-mono text-[10px] uppercase tracking-[0.15em] ${STATUS_CLASS[t.status]}`}
                >
                  {STATUS_LABEL[t.status]}
                </span>
              </td>
              <td className="py-3 font-mono text-muted">{t.credit_balance}</td>
              <td className="py-3 text-muted">{t.whatsapp_connected ? "Sim" : "Não"}</td>
              <td className="py-3 text-muted">
                {new Date(t.created_at).toLocaleDateString("pt-BR")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 3: Página da lista**

Criar `apps/web/src/app/admin/tenants/page.tsx`:

```tsx
import Link from "next/link";

import { AdminTenantsList } from "@/components/AdminTenantsList";

import { adminLogout } from "../actions";

export default function AdminTenantsPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <nav className="flex w-14 shrink-0 flex-col items-center justify-between bg-ink py-5">
        <div className="flex flex-col items-center gap-6">
          <span className="font-display text-2xl font-semibold text-ground" aria-label="Admin">
            A.
          </span>
          <Link
            href="/admin"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Dashboard
          </Link>
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground [writing-mode:vertical-rl]">
            Tenants
          </span>
        </div>
        <form action={adminLogout}>
          <button
            type="submit"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Sair
          </button>
        </form>
      </nav>
      <main className="flex-1 overflow-y-auto bg-ground">
        <AdminTenantsList />
      </main>
    </div>
  );
}
```

- [ ] **Step 4: Teste que falha (detalhe)**

Criar `apps/web/__tests__/AdminTenantDetail.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminTenantDetail } from "@/components/AdminTenantDetail";
import { adminBackendFetch } from "@/lib/admin-client-api";

vi.mock("@/lib/admin-client-api", () => ({
  adminBackendFetch: vi.fn(),
}));

const mockedFetch = adminBackendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("AdminTenantDetail", () => {
  it("mostra os dados do tenant, transações e arquivos de KB", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "t1",
        name: "Escritório A",
        email_contato: "a@escritorio.com",
        status: "active",
        credit_balance: 500,
        created_at: "2026-07-01T12:00:00Z",
        recent_transactions: [
          {
            id: "tx1",
            type: "purchase",
            amount_credits: 1000,
            description: "Compra do pacote Starter",
            created_at: "2026-07-01T12:00:00Z",
          },
        ],
        knowledge_base_files: [
          { id: "f1", filename: "regimento.pdf", status: "ready", uploaded_at: "2026-07-01T12:00:00Z" },
        ],
      }),
    });

    render(<AdminTenantDetail tenantId="t1" />);

    await waitFor(() => expect(screen.getByText("Escritório A")).toBeInTheDocument());
    expect(screen.getByText("Compra do pacote Starter")).toBeInTheDocument();
    expect(screen.getByText("regimento.pdf")).toBeInTheDocument();
  });

  it("mostra mensagem quando o tenant não é encontrado", async () => {
    mockedFetch.mockResolvedValue({ ok: false, status: 404 });

    render(<AdminTenantDetail tenantId="inexistente" />);

    await waitFor(() =>
      expect(screen.getByText("Escritório não encontrado.")).toBeInTheDocument(),
    );
  });
});
```

Run: `cd apps/web && npx --yes pnpm@9 test -- AdminTenantDetail`
Expected: FAIL — componente não existe.

- [ ] **Step 5: `AdminTenantDetail`**

Criar `apps/web/src/components/AdminTenantDetail.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { adminBackendFetch } from "@/lib/admin-client-api";

type TenantDetail = {
  id: string;
  name: string;
  email_contato: string;
  status: "active" | "suspended";
  credit_balance: number;
  created_at: string;
  recent_transactions: {
    id: string;
    type: string;
    amount_credits: number;
    description: string | null;
    created_at: string;
  }[];
  knowledge_base_files: {
    id: string;
    filename: string;
    status: string;
    uploaded_at: string;
  }[];
};

export function AdminTenantDetail({ tenantId }: { tenantId: string }) {
  const [tenant, setTenant] = useState<TenantDetail | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const response = await adminBackendFetch(`platform-admin/tenants/${tenantId}`);
        if (response.status === 404) {
          setNotFound(true);
        } else if (response.ok) {
          setTenant(await response.json());
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, [tenantId]);

  if (!loaded) {
    return <p className="p-8 text-sm text-muted">Carregando...</p>;
  }
  if (notFound || !tenant) {
    return <p className="p-8 text-sm text-danger">Escritório não encontrado.</p>;
  }

  return (
    <div className="flex flex-col gap-8 p-8">
      <div>
        <h1 className="font-display text-2xl font-semibold text-ink">{tenant.name}</h1>
        <p className="mt-1 text-sm text-muted">
          {tenant.email_contato} · {tenant.credit_balance} créditos · criado em{" "}
          {new Date(tenant.created_at).toLocaleDateString("pt-BR")}
        </p>
      </div>

      <div>
        <h2 className="font-display text-lg font-semibold text-ink">Transações recentes</h2>
        <ul className="mt-3 divide-y divide-line rounded-sm border border-line bg-surface">
          {tenant.recent_transactions.map((t) => (
            <li key={t.id} className="flex items-center justify-between px-4 py-3 text-sm">
              <span className="text-ink">{t.description ?? t.type}</span>
              <span className="font-mono text-muted">{t.amount_credits}</span>
            </li>
          ))}
          {tenant.recent_transactions.length === 0 && (
            <li className="px-4 py-3 text-sm text-muted">Sem transações ainda.</li>
          )}
        </ul>
      </div>

      <div>
        <h2 className="font-display text-lg font-semibold text-ink">Base de conhecimento</h2>
        <ul className="mt-3 divide-y divide-line rounded-sm border border-line bg-surface">
          {tenant.knowledge_base_files.map((f) => (
            <li key={f.id} className="flex items-center justify-between px-4 py-3 text-sm">
              <span className="text-ink">{f.filename}</span>
              <span className="text-muted">{f.status}</span>
            </li>
          ))}
          {tenant.knowledge_base_files.length === 0 && (
            <li className="px-4 py-3 text-sm text-muted">Nenhum arquivo enviado.</li>
          )}
        </ul>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Página de detalhe**

Criar `apps/web/src/app/admin/tenants/[id]/page.tsx`:

```tsx
import Link from "next/link";

import { AdminTenantDetail } from "@/components/AdminTenantDetail";

import { adminLogout } from "../../actions";

export default async function AdminTenantDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <div className="flex h-screen overflow-hidden">
      <nav className="flex w-14 shrink-0 flex-col items-center justify-between bg-ink py-5">
        <div className="flex flex-col items-center gap-6">
          <span className="font-display text-2xl font-semibold text-ground" aria-label="Admin">
            A.
          </span>
          <Link
            href="/admin"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Dashboard
          </Link>
          <Link
            href="/admin/tenants"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Tenants
          </Link>
        </div>
        <form action={adminLogout}>
          <button
            type="submit"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Sair
          </button>
        </form>
      </nav>
      <main className="flex-1 overflow-y-auto bg-ground">
        <AdminTenantDetail tenantId={id} />
      </main>
    </div>
  );
}
```

- [ ] **Step 7: Rodar os testes, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/components/AdminTenantsList.tsx apps/web/src/app/admin/tenants/page.tsx apps/web/src/components/AdminTenantDetail.tsx apps/web/src/app/admin/tenants/[id]/page.tsx apps/web/__tests__/AdminTenantsList.test.tsx apps/web/__tests__/AdminTenantDetail.test.tsx
git commit -m "feat(web): lista e detalhe de tenants no painel de administração"
```

---

### Task 7: Atualizar `CLAUDE.md`, criar o platform_admin de dev e verificação local

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: tudo das tasks anteriores.

- [ ] **Step 1: Atualizar o CLAUDE.md**

Seguindo o estilo das seções existentes:

- Seção "Painel de Administração da Plataforma": marcar como ✅ implementado (autenticação isolada, dashboard, lista/detalhe de tenants com auditoria) — manter registrado que ações (suspender, creditar) continuam de fora. Adicionar a lista de rotas novas (`/api/v1/platform-admin/{auth/*,dashboard,tenants}`) e a ressalva sobre receita não refletir preço histórico se um pacote mudar de valor.
- Seção "Multi-tenancy" → "Super-admin (plataforma)": trocar a menção a `BYPASSRLS`/queries dedicadas por uma nota explícita de que, **hoje**, isso funciona porque o `api` conecta como owner das tabelas (RLS inerte) — e que isso precisa ser revisitado quando essa pendência for resolvida (linkar as duas seções).
- Seção "Estado atual do repositório": `api` ganhou o painel de admin; `web` ganhou `/admin/*`.

- [ ] **Step 2: Criar um `platform_admin` de dev**

```bash
docker compose up -d --build api web
DATABASE_URL="postgresql+asyncpg://advoxs:changeme@localhost:5433/advoxs" REDIS_URL="redis://localhost:6379/0" JWT_SECRET="test" uv run alembic upgrade head  # dentro de apps/api
docker compose exec api uv run python scripts/seed_platform_admin.py --name "Admin Dev" --email admin@advoxs.com.br --password segredo123
```

- [ ] **Step 3: Verificação local**

1. `curl -X POST http://localhost:8000/api/v1/platform-admin/auth/login -H 'content-type: application/json' -d '{"email":"admin@advoxs.com.br","password":"segredo123"}'` — deve retornar um par de tokens.
2. Com o `access_token`, `curl http://localhost:8000/api/v1/platform-admin/dashboard -H "Authorization: Bearer <token>"` — deve retornar o JSON com todas as métricas (mesmo que zeradas, se não houver dado real ainda).
3. Confirmar que um token de **tenant** (do `admin@demo.com` do seed normal) usado em `/platform-admin/dashboard` retorna `401` — prova o isolamento de secret/type.
4. Confirmar que o token do **platform_admin** usado em `/api/v1/conversations` (rota de tenant) também retorna `401` — prova o isolamento nos dois sentidos.
5. Acessar `http://localhost:3001/admin` sem sessão — deve redirecionar pra `/admin/login`.
6. Logar em `/admin/login` e ver o dashboard renderizado com as métricas.
7. Acessar `/admin/tenants`, abrir o detalhe de um tenant, e confirmar (via `psql` ou log) que uma linha nova apareceu em `admin_audit_logs`.

Expected: todos os passos funcionam; o isolamento de sessão (passos 3 e 4) é o mais importante de confirmar.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: painel de administração da plataforma documentado no CLAUDE.md"
```
