# Perfil do Escritório Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Página `/perfil` no painel do tenant — nome do escritório editável, upload de logo, troca de senha, botão de sair — mais o fix do proxy binário necessário para servir a logo.

**Architecture:** Rotas novas tenant-scoped no `api` (`/profile`, `/profile/password`, `/profile/logo`) espelhando o padrão de upload já usado em `knowledge_base.py`. O proxy autenticado do `web` passa a repassar bytes crus (`arrayBuffer`) em vez de `text()`, permitindo servir a logo via `<img>`.

**Tech Stack:** FastAPI + SQLAlchemy async (api), Next.js 15 App Router + React (web).

## Global Constraints

- **Troca de senha exige a senha atual** (`verify_password` contra o hash do usuário autenticado); nova senha `Field(min_length=8)`, mesma regra do cadastro.
- **Logo**: PNG/JPG até 2 MB, path fixo por tenant (`{logo_upload_dir}/{tenant_id}.{ext}`), sobrescreve upload anterior sem versionamento.
- **Sem troca de e-mail nesta entrega** — nem do tenant (`email_contato`) nem do usuário.
- **Fix do proxy binário escopado ao proxy de tenant** (`/api/backend/[...path]/route.ts`) — o proxy de admin não serve arquivo nenhum, fica intocado.
- **Sair da conta reaproveita a Server Action `logout` já existente** (`@/app/conversas/actions`) — não criar uma segunda implementação de logout.
- Mensagens/comentários em pt-BR com acentuação correta.
- Comandos: `apps/api` → `uv run pytest tests/unit`, `uv run ruff check .`, `uv run ruff format --check .`. `apps/web` → `pnpm test`, `pnpm lint`, `pnpm build` (via `npx --yes pnpm@9 <comando>` se `pnpm` não estiver global).

---

### Task 1: `api` — migration, schemas e rotas de perfil (nome + senha)

**Files:**
- Create: `apps/api/alembic/versions/0005_tenant_logo_filename.py`
- Modify: `apps/api/app/models/tenant.py`
- Create: `apps/api/app/schemas/profile.py`
- Create: `apps/api/app/services/profile.py`
- Create: `apps/api/app/api/v1/profile.py`
- Modify: `apps/api/app/api/v1/router.py`
- Test: `apps/api/tests/unit/test_profile_service.py`
- Test: `apps/api/tests/unit/test_profile_routes.py`

**Interfaces:**
- Consumes: `TenantContext`/`get_current_tenant`/`get_tenant_session` (`app/api/deps.py`); `Tenant`/`User` (`app/models`); `hash_password`/`verify_password` (`app/core/security.py`).
- Produces: `update_tenant_name(session, tenant_id, name) -> Tenant`, `change_password(session, user_id, current_password, new_password) -> None` (levanta `InvalidCurrentPasswordError`) em `app.services.profile`; `GET/PATCH /api/v1/profile`, `POST /api/v1/profile/password`. Schema `ProfileOut` com o campo `has_logo: bool` (a Task 2 preenche de verdade; nesta task sempre `False`, já que `logo_filename` ainda não é gravado por nenhuma rota).

- [ ] **Step 1: Migration**

Criar `apps/api/alembic/versions/0005_tenant_logo_filename.py`:

```python
"""logo_filename em tenants

Nome do arquivo de logo salvo no volume logo_uploads — nullable, tenant
sem logo enviada ainda não tem essa coluna preenchida.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("logo_filename", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "logo_filename")
```

- [ ] **Step 2: Model**

Em `apps/api/app/models/tenant.py`, adicionar o import de `String` já existe; adicionar a coluna após `email_contato`:

```python
    email_contato: Mapped[str] = mapped_column(String, nullable=False)
    logo_filename: Mapped[str | None] = mapped_column(String)
```

- [ ] **Step 3: Schemas**

Criar `apps/api/app/schemas/profile.py`:

```python
from pydantic import BaseModel, EmailStr, Field


class ProfileOut(BaseModel):
    tenant_name: str
    email_contato: str
    has_logo: bool
    user_name: str
    user_email: str


class ProfileUpdateRequest(BaseModel):
    tenant_name: str = Field(min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)
```

- [ ] **Step 4: Escrever os testes do service que falham**

Criar `apps/api/tests/unit/test_profile_service.py`:

```python
import uuid
from types import SimpleNamespace

import pytest

from app.core.security import hash_password
from app.services.profile import InvalidCurrentPasswordError, change_password, update_tenant_name

TENANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def _tenant(name: str = "Escritório Antigo") -> SimpleNamespace:
    return SimpleNamespace(id=TENANT_ID, name=name)


def _user(password: str = "senha-atual") -> SimpleNamespace:
    return SimpleNamespace(id=USER_ID, password_hash=hash_password(password))


class FakeSession:
    def __init__(self, tenant=None, user=None):
        self._tenant = tenant
        self._user = user
        self.committed = False

    async def get(self, model, id_):
        if model.__name__ == "Tenant":
            return self._tenant
        return self._user

    async def commit(self):
        self.committed = True


class TestUpdateTenantName:
    async def test_atualiza_o_nome_do_tenant(self) -> None:
        tenant = _tenant()
        session = FakeSession(tenant=tenant)

        result = await update_tenant_name(session, TENANT_ID, "Escritório Novo")

        assert result.name == "Escritório Novo"
        assert session.committed is True


class TestChangePassword:
    async def test_senha_atual_incorreta_levanta_erro(self) -> None:
        user = _user(password="senha-atual")
        session = FakeSession(user=user)

        with pytest.raises(InvalidCurrentPasswordError):
            await change_password(session, USER_ID, "senha-errada", "nova-senha-123")

        assert session.committed is False

    async def test_senha_atual_correta_atualiza_o_hash(self) -> None:
        user = _user(password="senha-atual")
        old_hash = user.password_hash
        session = FakeSession(user=user)

        await change_password(session, USER_ID, "senha-atual", "nova-senha-123")

        assert user.password_hash != old_hash
        assert session.committed is True
```

- [ ] **Step 5: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_profile_service.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'app.services.profile'`.

- [ ] **Step 6: Service**

Criar `apps/api/app/services/profile.py`:

```python
"""Atualização de dados do escritório e troca de senha do usuário logado."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models import Tenant, User


class InvalidCurrentPasswordError(Exception):
    """Senha atual não confere — mapeada para 400 na rota."""


async def update_tenant_name(session: AsyncSession, tenant_id: uuid.UUID, name: str) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    tenant.name = name
    await session.commit()
    return tenant


async def change_password(
    session: AsyncSession, user_id: uuid.UUID, current_password: str, new_password: str
) -> None:
    user = await session.get(User, user_id)
    if not verify_password(current_password, user.password_hash):
        raise InvalidCurrentPasswordError("Senha atual incorreta")
    user.password_hash = hash_password(new_password)
    await session.commit()
```

- [ ] **Step 7: Rodar o teste do service e ver passar**

Run: `cd apps/api && uv run pytest tests/unit/test_profile_service.py -v`
Expected: PASS (3/3).

- [ ] **Step 8: Escrever os testes da rota que falham**

Criar `apps/api/tests/unit/test_profile_routes.py`:

```python
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.profile as profile_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.main import app
from app.services.profile import InvalidCurrentPasswordError

TENANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def _tenant(logo: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        name="Escritório Teste", email_contato="a@b.com", logo_filename=logo
    )


def _user() -> SimpleNamespace:
    return SimpleNamespace(name="Fulano", email="fulano@b.com")


@pytest.fixture
def session():
    mock = AsyncMock()
    return mock


@pytest.fixture
def client(session):
    async def override_tenant():
        return TenantContext(user_id=USER_ID, tenant_id=TENANT_ID, role="admin")

    async def override_session():
        yield session

    app.dependency_overrides[get_current_tenant] = override_tenant
    app.dependency_overrides[get_tenant_session] = override_session
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestGetProfile:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).get("/api/v1/profile")
        assert response.status_code == 401

    def test_retorna_dados_do_tenant_e_do_usuario(self, client, session) -> None:
        session.get = AsyncMock(side_effect=[_tenant(), _user()])

        response = client.get("/api/v1/profile")

        assert response.status_code == 200
        body = response.json()
        assert body["tenant_name"] == "Escritório Teste"
        assert body["has_logo"] is False
        assert body["user_name"] == "Fulano"

    def test_has_logo_true_quando_tenant_tem_logo(self, client, session) -> None:
        session.get = AsyncMock(side_effect=[_tenant(logo="abc.png"), _user()])

        response = client.get("/api/v1/profile")

        assert response.json()["has_logo"] is True


class TestUpdateProfile:
    def test_atualiza_o_nome(self, client, monkeypatch) -> None:
        update = AsyncMock(return_value=_tenant())
        monkeypatch.setattr(profile_module, "update_tenant_name", update)

        response = client.patch("/api/v1/profile", json={"tenant_name": "Novo Nome"})

        assert response.status_code == 200
        update.assert_awaited_once()
        assert update.await_args.args[1] == TENANT_ID
        assert update.await_args.args[2] == "Novo Nome"

    def test_nome_vazio_retorna_422(self, client) -> None:
        response = client.patch("/api/v1/profile", json={"tenant_name": ""})
        assert response.status_code == 422


class TestChangePasswordRoute:
    def test_senha_atual_errada_retorna_400(self, client, monkeypatch) -> None:
        change = AsyncMock(side_effect=InvalidCurrentPasswordError("Senha atual incorreta"))
        monkeypatch.setattr(profile_module, "change_password", change)

        response = client.post(
            "/api/v1/profile/password",
            json={"current_password": "errada", "new_password": "nova12345"},
        )

        assert response.status_code == 400

    def test_sucesso_retorna_204(self, client, monkeypatch) -> None:
        change = AsyncMock()
        monkeypatch.setattr(profile_module, "change_password", change)

        response = client.post(
            "/api/v1/profile/password",
            json={"current_password": "certa", "new_password": "nova12345"},
        )

        assert response.status_code == 204

    def test_senha_nova_curta_retorna_422(self, client) -> None:
        response = client.post(
            "/api/v1/profile/password",
            json={"current_password": "certa", "new_password": "curta"},
        )
        assert response.status_code == 422
```

- [ ] **Step 9: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_profile_routes.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'app.api.v1.profile'`.

- [ ] **Step 10: Rotas**

Criar `apps/api/app/api/v1/profile.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.models import Tenant, User
from app.schemas.profile import ChangePasswordRequest, ProfileOut, ProfileUpdateRequest
from app.services.profile import InvalidCurrentPasswordError, change_password, update_tenant_name

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("")
async def get_profile(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ProfileOut:
    tenant = await session.get(Tenant, ctx.tenant_id)
    user = await session.get(User, ctx.user_id)
    return ProfileOut(
        tenant_name=tenant.name,
        email_contato=tenant.email_contato,
        has_logo=tenant.logo_filename is not None,
        user_name=user.name,
        user_email=user.email,
    )


@router.patch("")
async def update_profile(
    body: ProfileUpdateRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ProfileOut:
    tenant = await update_tenant_name(session, ctx.tenant_id, body.tenant_name)
    user = await session.get(User, ctx.user_id)
    return ProfileOut(
        tenant_name=tenant.name,
        email_contato=tenant.email_contato,
        has_logo=tenant.logo_filename is not None,
        user_name=user.name,
        user_email=user.email,
    )


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password_route(
    body: ChangePasswordRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    try:
        await change_password(session, ctx.user_id, body.current_password, body.new_password)
    except InvalidCurrentPasswordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
```

- [ ] **Step 11: Registrar no router principal**

Em `apps/api/app/api/v1/router.py`, adicionar:

```python
from app.api.v1.profile import router as profile_router
```

```python
api_router.include_router(profile_router)
```

- [ ] **Step 12: Rodar a suíte completa, migration e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos PASS, ruff limpo.

Run (ajustar credenciais conforme seu Postgres local): `DATABASE_URL="postgresql+asyncpg://advoxs:changeme@localhost:5433/advoxs" REDIS_URL="redis://localhost:6379/0" JWT_SECRET="test" uv run alembic upgrade head`
Expected: aplica a `0005` sem erro.

- [ ] **Step 13: Commit**

```bash
git add apps/api/alembic/versions/0005_tenant_logo_filename.py apps/api/app/models/tenant.py apps/api/app/schemas/profile.py apps/api/app/services/profile.py apps/api/app/api/v1/profile.py apps/api/app/api/v1/router.py apps/api/tests/unit/test_profile_service.py apps/api/tests/unit/test_profile_routes.py
git commit -m "feat(api): rotas de perfil do escritório (nome e troca de senha)"
```

---

### Task 2: `api` — upload e serving da logo

**Files:**
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/api/v1/profile.py`
- Modify: `apps/api/app/schemas/profile.py`
- Modify: `apps/api/tests/unit/test_profile_routes.py`
- Modify: `docker-compose.yml`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `settings.logo_upload_dir` (novo); `Tenant.logo_filename` (Task 1).
- Produces: `POST /api/v1/profile/logo`, `GET /api/v1/profile/logo`.

- [ ] **Step 1: Config**

Em `apps/api/app/core/config.py`, adicionar (junto das outras envs de upload):

```python
    # Logo do escritório — path fixo por tenant, sobrescrito a cada upload.
    logo_upload_dir: str = "/data/logo_uploads"
    logo_max_file_size_bytes: int = 2 * 1024 * 1024
```

- [ ] **Step 2: Volume no compose**

Em `docker-compose.yml`, na seção `volumes:` do topo do arquivo, adicionar `logo_uploads:` (junto de `kb_uploads:`). No serviço `api`, adicionar o mount:

```yaml
      - logo_uploads:/data/logo_uploads
```

Em `.env.example`, não é necessário adicionar nada (o path tem default hardcoded, sem env obrigatória).

- [ ] **Step 3: Escrever os testes que falham**

Em `apps/api/tests/unit/test_profile_routes.py`, adicionar (após a classe `TestChangePasswordRoute`, usando `tmp_path` do pytest e `monkeypatch` de `settings.logo_upload_dir`):

```python
class TestUploadLogo:
    def test_extensao_nao_suportada_retorna_400(self, client, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(profile_module.settings, "logo_upload_dir", str(tmp_path))

        response = client.post(
            "/api/v1/profile/logo",
            files={"file": ("logo.gif", b"fake-gif-bytes", "image/gif")},
        )

        assert response.status_code == 400

    def test_arquivo_grande_retorna_413(self, client, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(profile_module.settings, "logo_upload_dir", str(tmp_path))
        monkeypatch.setattr(profile_module.settings, "logo_max_file_size_bytes", 10)

        response = client.post(
            "/api/v1/profile/logo",
            files={"file": ("logo.png", b"0123456789ABC", "image/png")},
        )

        assert response.status_code == 413

    def test_upload_valido_grava_o_arquivo_e_atualiza_o_tenant(
        self, client, session, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.setattr(profile_module.settings, "logo_upload_dir", str(tmp_path))
        tenant = _tenant()
        session.get = AsyncMock(return_value=tenant)

        response = client.post(
            "/api/v1/profile/logo",
            files={"file": ("logo.png", b"fake-png-bytes", "image/png")},
        )

        assert response.status_code == 200
        assert tenant.logo_filename == f"{TENANT_ID}.png"
        assert (tmp_path / f"{TENANT_ID}.png").read_bytes() == b"fake-png-bytes"
        session.commit.assert_awaited_once()


class TestGetLogo:
    def test_sem_logo_retorna_404(self, client, session) -> None:
        session.get = AsyncMock(return_value=_tenant(logo=None))

        response = client.get("/api/v1/profile/logo")

        assert response.status_code == 404

    def test_com_logo_retorna_o_arquivo(self, client, session, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(profile_module.settings, "logo_upload_dir", str(tmp_path))
        (tmp_path / f"{TENANT_ID}.png").write_bytes(b"fake-png-bytes")
        session.get = AsyncMock(return_value=_tenant(logo=f"{TENANT_ID}.png"))

        response = client.get("/api/v1/profile/logo")

        assert response.status_code == 200
        assert response.content == b"fake-png-bytes"
        assert response.headers["content-type"] == "image/png"
```

Adicionar o import de `profile_module.settings` — no topo do arquivo de teste já existe `import app.api.v1.profile as profile_module`; adicionar também `from app.core.config import settings` não é necessário, pois o teste acessa via `profile_module.settings` (o módulo da rota importa `settings` do config — ver Step 4).

- [ ] **Step 4: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_profile_routes.py -v`
Expected: FAIL — `AttributeError` (rotas de logo não existem ainda).

- [ ] **Step 5: Schema de erro genérico (reaproveitar `HTTPException`, sem schema novo) e rotas**

Em `apps/api/app/api/v1/profile.py`, adicionar os imports necessários no topo:

```python
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.core.config import settings
from app.models import Tenant, User
from app.schemas.profile import ChangePasswordRequest, ProfileOut, ProfileUpdateRequest
from app.services.profile import InvalidCurrentPasswordError, change_password, update_tenant_name

router = APIRouter(prefix="/profile", tags=["profile"])

ALLOWED_LOGO_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
```

(a linha `from fastapi import APIRouter, Depends, HTTPException, status` antiga é substituída por essa mais completa; os demais imports já existentes continuam.)

Adicionar, ao final do arquivo:

```python
@router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> ProfileOut:
    filename = file.filename or ""
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_LOGO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato não suportado — envie PNG ou JPG",
        )

    data = await file.read()
    if len(data) > settings.logo_max_file_size_bytes:
        limite_mb = settings.logo_max_file_size_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Arquivo maior que {limite_mb} MB",
        )

    upload_dir = Path(settings.logo_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{ctx.tenant_id}{extension}"
    (upload_dir / stored_filename).write_bytes(data)

    tenant = await session.get(Tenant, ctx.tenant_id)
    tenant.logo_filename = stored_filename
    await session.commit()

    user = await session.get(User, ctx.user_id)
    return ProfileOut(
        tenant_name=tenant.name,
        email_contato=tenant.email_contato,
        has_logo=True,
        user_name=user.name,
        user_email=user.email,
    )


@router.get("/logo")
async def get_logo(
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> Response:
    tenant = await session.get(Tenant, ctx.tenant_id)
    if tenant.logo_filename is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sem logo cadastrada")

    path = Path(settings.logo_upload_dir) / tenant.logo_filename
    extension = path.suffix.lower()
    content_type = ALLOWED_LOGO_EXTENSIONS.get(extension, "application/octet-stream")
    return Response(content=path.read_bytes(), media_type=content_type)
```

- [ ] **Step 6: Rodar os testes e ver passar**

Run: `cd apps/api && uv run pytest tests/unit/test_profile_routes.py -v`
Expected: PASS em todos.

- [ ] **Step 7: Rodar a suíte completa e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: todos PASS, ruff limpo.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/core/config.py apps/api/app/api/v1/profile.py apps/api/tests/unit/test_profile_routes.py docker-compose.yml
git commit -m "feat(api): upload e serving da logo do escritório"
```

---

### Task 3: `web` — fix do proxy binário

**Files:**
- Modify: `apps/web/src/app/api/backend/[...path]/route.ts`
- Test: `apps/web/__tests__/backend-proxy-binary.test.ts`

**Interfaces:**
- Consumes: nenhuma nova — mudança interna ao proxy já existente.
- Produces: o proxy passa a repassar `ArrayBuffer` em vez de `string`, preservando bytes binários.

**Contexto**: o proxy hoje faz `const payload = await response.text(); return new NextResponse(payload, ...)`. Isso decodifica a resposta como UTF-8 e a reserializa como string — qualquer byte que não seja UTF-8 válido é corrompido. Para servir a logo via `<img src="/api/backend/profile/logo">`, o proxy precisa devolver os bytes exatamente como veio do `api`.

- [ ] **Step 1: Escrever o teste que falha**

Este proxy é uma rota de servidor Next.js (`route.ts`), não um componente React — o teste unitário chama a função exportada diretamente, mockando `fetch` global e `next/headers`. Criar `apps/web/__tests__/backend-proxy-binary.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/headers", () => ({
  cookies: vi.fn(async () => ({
    get: vi.fn((name: string) => (name === "access_token" ? { value: "token-valido" } : undefined)),
  })),
}));

import { GET } from "@/app/api/backend/[...path]/route";

const mockedFetch = vi.fn();

beforeEach(() => {
  mockedFetch.mockReset();
  vi.stubGlobal("fetch", mockedFetch);
});

function makeRequest(path: string): Request {
  return new Request(`http://localhost:3000/api/backend/${path}`, { method: "GET" });
}

describe("proxy binário", () => {
  it("repassa bytes não-UTF-8 sem corromper (ex: PNG)", async () => {
    // Um PNG começa com esses 8 bytes de assinatura — não é UTF-8 válido.
    const pngBytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0xff, 0xd8]);
    mockedFetch.mockResolvedValue({
      status: 200,
      headers: new Headers({ "content-type": "image/png" }),
      arrayBuffer: async () => pngBytes.buffer,
    });

    const response = await GET(makeRequest("profile/logo"), {
      params: Promise.resolve({ path: ["profile", "logo"] }),
    });

    const received = new Uint8Array(await response.arrayBuffer());
    expect(Array.from(received)).toEqual(Array.from(pngBytes));
    expect(response.headers.get("content-type")).toBe("image/png");
  });
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/web && npx --yes pnpm@9 test -- backend-proxy-binary`
Expected: FAIL — o teste mocka `arrayBuffer()` na resposta do fetch, mas o proxy hoje chama `.text()`, então o mock não bate com a chamada real e o teste falha (o retorno de `.text()` não estará definido no mock, gerando erro).

- [ ] **Step 3: Trocar `.text()` por `.arrayBuffer()`**

Em `apps/web/src/app/api/backend/[...path]/route.ts`, trocar o final da função `handle`:

```ts
  const payload = await response.text();
  return new NextResponse(payload, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
    },
  });
```

por:

```ts
  const payload = await response.arrayBuffer();
  return new NextResponse(payload, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
    },
  });
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/web && npx --yes pnpm@9 test -- backend-proxy-binary`
Expected: PASS (1/1).

- [ ] **Step 5: Rodar toda a suíte, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde — nenhuma rota existente depende de `response.text()` retornando exatamente uma `string` (o `NextResponse` aceita `ArrayBuffer` como body normalmente; respostas JSON continuam funcionando porque JSON é sempre UTF-8 válido, e `ArrayBuffer` de bytes UTF-8 é indistinguível em efeito do `string` anterior pro cliente).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/app/api/backend/[...path]/route.ts apps/web/__tests__/backend-proxy-binary.test.ts
git commit -m "fix(web): proxy autenticado repassa bytes crus (corrigia respostas binárias)"
```

---

### Task 4: `web` — página `/perfil`, upload de logo, troca de senha

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/backend.ts`
- Modify: `apps/web/src/components/TenantNav.tsx`
- Modify: `apps/web/__tests__/TenantNav.test.tsx`
- Create: `apps/web/src/components/ProfilePanel.tsx`
- Create: `apps/web/src/app/perfil/page.tsx`
- Modify: `apps/web/src/middleware.ts`
- Test: `apps/web/__tests__/ProfilePanel.test.tsx`

**Interfaces:**
- Consumes: `backendFetch` (`@/lib/client-api`); `logout` (`@/app/conversas/actions`); `GET/PATCH profile`, `POST profile/password`, `POST/GET profile/logo` (Tasks 1-2, forma de `ProfileOut`).
- Produces: tipo `Profile` em `@/lib/types`; `ProfilePanel()` em `@/components/ProfilePanel`; `TenantNavItem` ganha `"perfil"`.

- [ ] **Step 1: Tipo `Profile`**

Em `apps/web/src/lib/types.ts`, adicionar ao final:

```ts
export interface Profile {
  tenant_name: string;
  email_contato: string;
  has_logo: boolean;
  user_name: string;
  user_email: string;
}
```

- [ ] **Step 2: Allowlist do proxy**

Em `apps/web/src/lib/backend.ts`, adicionar `"profile"` à lista `ALLOWED_PREFIXES`.

- [ ] **Step 3: Item "Perfil" no `TenantNav`**

Em `apps/web/src/components/TenantNav.tsx`, trocar o tipo e a lista:

```tsx
type TenantNavItem = "inicio" | "conversas" | "base" | "config" | "creditos" | "perfil";

const ITEMS: { key: TenantNavItem; href: string; label: string }[] = [
  { key: "inicio", href: "/inicio", label: "Início" },
  { key: "conversas", href: "/conversas", label: "Conversas" },
  { key: "base", href: "/base-de-conhecimento", label: "Base" },
  { key: "config", href: "/configuracoes/whatsapp", label: "Config" },
  { key: "creditos", href: "/creditos", label: "Créditos" },
  { key: "perfil", href: "/perfil", label: "Perfil" },
];
```

Em `apps/web/__tests__/TenantNav.test.tsx`, adicionar ao primeiro teste (`active="conversas"`):

```tsx
    expect(screen.getByText("Perfil").closest("a")).toHaveAttribute("href", "/perfil");
```

E adicionar um teste novo:

```tsx
  it("marca perfil como ativo quando active='perfil'", () => {
    render(<TenantNav active="perfil" />);

    expect(screen.getByText("Perfil").closest("a")).toBeNull();
    expect(screen.getByText("Início").closest("a")).toHaveAttribute("href", "/inicio");
  });
```

- [ ] **Step 4: Escrever o teste do `ProfilePanel` que falha**

Criar `apps/web/__tests__/ProfilePanel.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProfilePanel } from "@/components/ProfilePanel";
import { backendFetch } from "@/lib/client-api";
import type { Profile } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const PROFILE: Profile = {
  tenant_name: "Escritório Teste",
  email_contato: "a@b.com",
  has_logo: false,
  user_name: "Fulano",
  user_email: "fulano@b.com",
};

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("ProfilePanel", () => {
  it("carrega e exibe os dados do perfil", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => PROFILE });

    render(<ProfilePanel />);

    await waitFor(() =>
      expect(screen.getByDisplayValue("Escritório Teste")).toBeInTheDocument(),
    );
    expect(screen.getByText("fulano@b.com")).toBeInTheDocument();
  });

  it("salva o nome do escritório", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "profile" && (!init || init.method === undefined)) {
        return { ok: true, json: async () => PROFILE };
      }
      if (path === "profile" && init?.method === "PATCH") {
        expect(JSON.parse(init.body as string)).toEqual({ tenant_name: "Novo Nome" });
        return { ok: true, json: async () => ({ ...PROFILE, tenant_name: "Novo Nome" }) };
      }
      throw new Error(`chamada inesperada: ${path}`);
    });

    render(<ProfilePanel />);
    await waitFor(() => expect(screen.getByDisplayValue("Escritório Teste")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Nome do escritório"), {
      target: { value: "Novo Nome" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Salvar nome" }));

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith("profile", expect.objectContaining({ method: "PATCH" })),
    );
  });

  it("mostra erro quando a senha atual está errada", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "profile" && !init) {
        return { ok: true, json: async () => PROFILE };
      }
      if (path === "profile/password") {
        return { ok: false, status: 400, json: async () => ({ detail: "Senha atual incorreta" }) };
      }
      throw new Error(`chamada inesperada: ${path}`);
    });

    render(<ProfilePanel />);
    await waitFor(() => expect(screen.getByDisplayValue("Escritório Teste")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Senha atual"), { target: { value: "errada" } });
    fireEvent.change(screen.getByLabelText("Nova senha"), { target: { value: "nova12345" } });
    fireEvent.change(screen.getByLabelText("Confirmar nova senha"), {
      target: { value: "nova12345" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Trocar senha" }));

    await waitFor(() => expect(screen.getByText("Senha atual incorreta")).toBeInTheDocument());
  });

  it("mostra erro quando a confirmação de senha não bate, sem chamar a API", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => PROFILE });

    render(<ProfilePanel />);
    await waitFor(() => expect(screen.getByDisplayValue("Escritório Teste")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Senha atual"), { target: { value: "atual123" } });
    fireEvent.change(screen.getByLabelText("Nova senha"), { target: { value: "nova12345" } });
    fireEvent.change(screen.getByLabelText("Confirmar nova senha"), {
      target: { value: "diferente" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Trocar senha" }));

    expect(screen.getByText("As senhas não coincidem.")).toBeInTheDocument();
    expect(mockedFetch).not.toHaveBeenCalledWith(
      "profile/password",
      expect.anything(),
    );
  });

  it("renderiza o botão Sair da conta", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => PROFILE });

    render(<ProfilePanel />);
    await waitFor(() => expect(screen.getByDisplayValue("Escritório Teste")).toBeInTheDocument());

    expect(screen.getByRole("button", { name: "Sair da conta" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 5: Rodar e ver falhar**

Run: `cd apps/web && npx --yes pnpm@9 test -- ProfilePanel`
Expected: FAIL — `@/components/ProfilePanel` não existe.

- [ ] **Step 6: Criar `ProfilePanel`**

Criar `apps/web/src/components/ProfilePanel.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { logout } from "@/app/conversas/actions";
import { backendFetch } from "@/lib/client-api";
import type { Profile } from "@/lib/types";

export function ProfilePanel() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [tenantName, setTenantName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [nameSaved, setNameSaved] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSaved, setPasswordSaved] = useState(false);
  const [logoError, setLogoError] = useState<string | null>(null);
  const [logoVersion, setLogoVersion] = useState(0);

  useEffect(() => {
    async function load() {
      try {
        const response = await backendFetch("profile");
        if (response.ok) {
          const body: Profile = await response.json();
          setProfile(body);
          setTenantName(body.tenant_name);
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, []);

  async function handleSaveName() {
    setNameError(null);
    setNameSaved(false);
    const response = await backendFetch("profile", {
      method: "PATCH",
      body: JSON.stringify({ tenant_name: tenantName }),
    });
    if (response.ok) {
      const body: Profile = await response.json();
      setProfile(body);
      setNameSaved(true);
    } else {
      setNameError("Não foi possível salvar. Tente novamente.");
    }
  }

  async function handleChangePassword() {
    setPasswordError(null);
    setPasswordSaved(false);
    if (newPassword !== confirmPassword) {
      setPasswordError("As senhas não coincidem.");
      return;
    }
    const response = await backendFetch("profile/password", {
      method: "POST",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
    if (response.ok) {
      setPasswordSaved(true);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } else {
      const body = await response.json().catch(() => null);
      setPasswordError(
        typeof body?.detail === "string" ? body.detail : "Não foi possível trocar a senha.",
      );
    }
  }

  async function handleUploadLogo(file: File) {
    setLogoError(null);
    const formData = new FormData();
    formData.append("file", file);
    const response = await backendFetch("profile/logo", { method: "POST", body: formData });
    if (response.ok) {
      const body: Profile = await response.json();
      setProfile(body);
      setLogoVersion((v) => v + 1);
    } else {
      setLogoError("Não foi possível enviar a logo. Envie um PNG ou JPG de até 2 MB.");
    }
  }

  if (!loaded) {
    return <p className="p-8 text-sm text-muted">Carregando...</p>;
  }
  if (!profile) {
    return <p className="p-8 text-sm text-danger">Não foi possível carregar o perfil.</p>;
  }

  return (
    <div className="flex flex-col gap-8 p-8">
      <section className="flex flex-col gap-4 rounded-sm border border-line bg-surface p-6">
        <h2 className="font-display text-lg font-semibold text-ink">Dados do escritório</h2>

        <div className="flex items-center gap-4">
          {profile.has_logo ? (
            <img
              key={logoVersion}
              src="/api/backend/profile/logo"
              alt="Logo do escritório"
              className="h-16 w-16 rounded-sm object-cover"
            />
          ) : (
            <div className="flex h-16 w-16 items-center justify-center rounded-sm bg-ink font-display text-2xl font-semibold text-ground">
              A.
            </div>
          )}
          <label className="cursor-pointer rounded-sm border border-line px-3 py-1.5 text-sm text-ink hover:bg-ground">
            Alterar logo
            <input
              type="file"
              accept="image/png,image/jpeg"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void handleUploadLogo(file);
              }}
            />
          </label>
        </div>
        {logoError && <p className="text-sm text-danger">{logoError}</p>}

        <div className="flex flex-col gap-1.5">
          <label htmlFor="tenant-name" className="text-sm font-medium text-ink">
            Nome do escritório
          </label>
          <input
            id="tenant-name"
            type="text"
            value={tenantName}
            onChange={(event) => setTenantName(event.target.value)}
            className="rounded-sm border border-line bg-ground px-3 py-2.5 text-sm"
          />
        </div>
        {nameError && <p className="text-sm text-danger">{nameError}</p>}
        {nameSaved && <p className="text-sm text-accent">Nome salvo.</p>}
        <button
          type="button"
          onClick={() => void handleSaveName()}
          className="self-start rounded-sm bg-accent px-4 py-2 text-sm font-medium text-surface hover:bg-ink"
        >
          Salvar nome
        </button>

        <p className="text-sm text-muted">
          Usuário: {profile.user_name} ({profile.user_email})
        </p>
      </section>

      <section className="flex flex-col gap-4 rounded-sm border border-line bg-surface p-6">
        <h2 className="font-display text-lg font-semibold text-ink">Trocar senha</h2>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="current-password" className="text-sm font-medium text-ink">
            Senha atual
          </label>
          <input
            id="current-password"
            type="password"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            className="rounded-sm border border-line bg-ground px-3 py-2.5 text-sm"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="new-password" className="text-sm font-medium text-ink">
            Nova senha
          </label>
          <input
            id="new-password"
            type="password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            className="rounded-sm border border-line bg-ground px-3 py-2.5 text-sm"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="confirm-password" className="text-sm font-medium text-ink">
            Confirmar nova senha
          </label>
          <input
            id="confirm-password"
            type="password"
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            className="rounded-sm border border-line bg-ground px-3 py-2.5 text-sm"
          />
        </div>
        {passwordError && <p className="text-sm text-danger">{passwordError}</p>}
        {passwordSaved && <p className="text-sm text-accent">Senha alterada.</p>}
        <button
          type="button"
          onClick={() => void handleChangePassword()}
          className="self-start rounded-sm bg-accent px-4 py-2 text-sm font-medium text-surface hover:bg-ink"
        >
          Trocar senha
        </button>
      </section>

      <section className="rounded-sm border border-danger/40 bg-surface p-6">
        <form action={logout}>
          <button
            type="submit"
            className="rounded-sm border border-danger px-4 py-2 text-sm font-medium text-danger hover:bg-danger/10"
          >
            Sair da conta
          </button>
        </form>
      </section>
    </div>
  );
}
```

- [ ] **Step 7: Rodar e ver passar**

Run: `cd apps/web && npx --yes pnpm@9 test -- ProfilePanel`
Expected: PASS (5/5).

- [ ] **Step 8: Página `/perfil` e middleware**

Criar `apps/web/src/app/perfil/page.tsx`:

```tsx
import { ProfilePanel } from "@/components/ProfilePanel";
import { TenantNav } from "@/components/TenantNav";

export default function PerfilPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="perfil" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <ProfilePanel />
      </main>
    </div>
  );
}
```

Em `apps/web/src/middleware.ts`, no `config.matcher`, adicionar `"/perfil/:path*"` (junto das outras entradas do painel do tenant).

- [ ] **Step 9: Rodar toda a suíte, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde; build lista `/perfil`.

- [ ] **Step 10: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/lib/backend.ts apps/web/src/components/TenantNav.tsx apps/web/__tests__/TenantNav.test.tsx apps/web/src/components/ProfilePanel.tsx apps/web/src/app/perfil/page.tsx apps/web/src/middleware.ts apps/web/__tests__/ProfilePanel.test.tsx
git commit -m "feat(web): página de perfil do escritório (/perfil)"
```

---

### Task 5: `web` — logo na `TenantNav` + `CLAUDE.md` + verificação local

**Files:**
- Modify: `apps/web/src/components/TenantNav.tsx`
- Test: `apps/web/__tests__/TenantNav.test.tsx`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `GET profile` (Task 1, campo `has_logo`).

- [ ] **Step 1: Escrever o teste que falha**

Em `apps/web/__tests__/TenantNav.test.tsx`, adicionar o mock de `backendFetch` (o componente precisa buscar `has_logo`) e um teste novo:

```tsx
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;
```

(adicionar esses 3 blocos no topo do arquivo, junto dos imports/mocks já existentes — `TenantNav.test.tsx` hoje não mocka `client-api`, então este é o primeiro uso.)

```tsx
  it("mostra a logo quando o tenant tem uma (has_logo=true)", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ has_logo: true }) });

    render(<TenantNav active="conversas" />);

    await waitFor(() => expect(screen.getByAltText("Logo do escritório")).toBeInTheDocument());
  });

  it("mantém o monograma quando o tenant não tem logo", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ has_logo: false }) });

    render(<TenantNav active="conversas" />);

    await waitFor(() => expect(mockedFetch).toHaveBeenCalledWith("profile"));
    expect(screen.queryByAltText("Logo do escritório")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Advoxs")).toBeInTheDocument();
  });

  it("mantém o monograma quando a busca de perfil falha (fail-safe)", async () => {
    mockedFetch.mockRejectedValue(new Error("network error"));

    render(<TenantNav active="conversas" />);

    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    expect(screen.getByLabelText("Advoxs")).toBeInTheDocument();
  });
```

(o import de `waitFor` já existe implicitamente? confira o topo do arquivo — se `render`/`screen` vêm de `@testing-library/react` sem `waitFor`, adicionar `waitFor` a esse import.)

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/web && npx --yes pnpm@9 test -- TenantNav`
Expected: FAIL — os 3 testes novos falham porque o componente ainda não busca `has_logo` nem renderiza `<img alt="Logo do escritório">`.

- [ ] **Step 3: Buscar `has_logo` e renderizar a logo**

Em `apps/web/src/components/TenantNav.tsx`, tornar o componente client e buscar o perfil:

```tsx
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { logout } from "@/app/conversas/actions";
import { backendFetch } from "@/lib/client-api";

type TenantNavItem = "inicio" | "conversas" | "base" | "config" | "creditos" | "perfil";

const ITEMS: { key: TenantNavItem; href: string; label: string }[] = [
  { key: "inicio", href: "/inicio", label: "Início" },
  { key: "conversas", href: "/conversas", label: "Conversas" },
  { key: "base", href: "/base-de-conhecimento", label: "Base" },
  { key: "config", href: "/configuracoes/whatsapp", label: "Config" },
  { key: "creditos", href: "/creditos", label: "Créditos" },
  { key: "perfil", href: "/perfil", label: "Perfil" },
];

export function TenantNav({ active }: { active: TenantNavItem | null }) {
  const [hasLogo, setHasLogo] = useState(false);

  useEffect(() => {
    async function loadProfile() {
      try {
        const response = await backendFetch("profile");
        if (response.ok) {
          const body = await response.json();
          setHasLogo(Boolean(body.has_logo));
        }
      } catch {
        // fail-safe silencioso — mantém o monograma
      }
    }
    void loadProfile();
  }, []);

  return (
    <nav className="flex w-14 shrink-0 flex-col items-center justify-between bg-ink py-5">
      <div className="flex flex-col items-center gap-6">
        {hasLogo ? (
          <img
            src="/api/backend/profile/logo"
            alt="Logo do escritório"
            className="h-8 w-8 rounded-sm object-cover"
          />
        ) : (
          <span className="font-display text-2xl font-semibold text-ground" aria-label="Advoxs">
            A.
          </span>
        )}
        {ITEMS.map((item) =>
          item.key === active ? (
            <span
              key={item.key}
              className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground [writing-mode:vertical-rl]"
            >
              {item.label}
            </span>
          ) : (
            <Link
              key={item.key}
              href={item.href}
              className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
            >
              {item.label}
            </Link>
          ),
        )}
      </div>
      <form action={logout}>
        <button
          type="submit"
          className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
        >
          Sair
        </button>
      </form>
    </nav>
  );
}
```

(a diretiva `"use client"` é nova no topo do arquivo — antes o componente não tinha estado; confirme que isso não quebra nenhum uso em Server Component, já que `TenantNav` é usado nas páginas que já são Server Components — usar um Client Component como filho de um Server Component é sempre válido em Next.js App Router.)

- [ ] **Step 4: Rodar e ver passar**

Run: `cd apps/web && npx --yes pnpm@9 test -- TenantNav`
Expected: PASS em todos (os testes antigos que não mockavam `backendFetch` continuam passando, porque `mockedFetch` sem `.mockResolvedValue` configurado explicitamente por teste ainda retorna `undefined` de uma chamada não tratada — para garantir isso, adicione `beforeEach(() => { mockedFetch.mockReset(); mockedFetch.mockResolvedValue({ ok: false }); });` no topo do `describe`, cobrindo o default de todos os testes que não setam mock próprio).

- [ ] **Step 5: Rodar toda a suíte, lint e build**

Run: `cd apps/web && npx --yes pnpm@9 test && npx --yes pnpm@9 lint && npx --yes pnpm@9 build`
Expected: tudo verde.

- [ ] **Step 6: Atualizar o CLAUDE.md**

- Seção "Frontend": adicionar um item `- **`/perfil`** — ✅ implementada: dados do escritório (nome editável, logo com upload PNG/JPG até 2 MB), troca de senha (exige a senha atual), botão "Sair da conta". A logo, quando cadastrada, substitui o monograma na nav lateral.` (posicionar após o item de `/creditos`, antes de "Painel de Conversas").
- Seção "Estado atual do repositório", linha do `api`: acrescentar `/api/v1/profile` (perfil, troca de senha, logo) à lista de implementados.
- Seção "Estado atual do repositório", linha do `web`: acrescentar `/perfil` à lista de implementados.
- Nenhuma pendência precisa ser removida (esta feature não estava listada como pendência).

- [ ] **Step 7: Build e verificação local**

```bash
docker compose up -d --build api web
```

1. Login com o tenant de seed (`admin@demo.com`/`segredo123`), pegar o `access_token`.
2. `curl http://localhost:8000/api/v1/profile -H "Authorization: Bearer <token>"` — confirma o payload com `has_logo: false`.
3. `curl -X PATCH http://localhost:8000/api/v1/profile -H "Authorization: Bearer <token>" -H 'content-type: application/json' -d '{"tenant_name":"Escritório Renomeado"}'` — confirma `200` com o nome atualizado.
4. `curl -X POST http://localhost:8000/api/v1/profile/password -H "Authorization: Bearer <token>" -H 'content-type: application/json' -d '{"current_password":"senha-errada","new_password":"nova12345678"}'` — confirma `400`.
5. Acessar `http://localhost:3001/perfil` logado — confirmar que os 3 blocos renderizam, o nome atualizado aparece, e o botão "Sair da conta" funciona (desloga e redireciona pro `/login`).
6. Fazer upload de uma imagem PNG pequena pela UI — confirmar que a preview atualiza e que o monograma na nav é substituído pela logo em todas as páginas do painel.
7. Sem sessão, acessar `/perfil` — confirmar redirect pro `/login`.

Expected: todos os passos funcionam; o passo 6 (logo aparecendo na nav sem corromper via proxy) é o mais importante — prova que o fix do proxy binário (Task 3) funciona de ponta a ponta.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/components/TenantNav.tsx apps/web/__tests__/TenantNav.test.tsx CLAUDE.md
git commit -m "feat(web): logo do escritório na nav lateral + docs do perfil"
```
