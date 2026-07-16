# Login automático pós-pagamento — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Depois do pagamento do cadastro, o usuário cai logado direto em `/inicio` — via token one-time (entrega única no polling, troca única por JWT), sem nunca transformar o `session_id` da Stripe em credencial.

**Architecture:** Service novo `signup_tokens.py` encapsula as duas chaves Redis (handoff por `session_id` em claro + token hasheado → `user_id`, TTL 900s, GETDEL nos dois). O webhook gera o token best-effort após criar a conta; `GET /signup/status` entrega o token uma vez; `POST /auth/signup-login` troca por par JWT reutilizando os helpers do login. No web, server action nova seta os cookies e redireciona.

**Tech Stack:** FastAPI + redis.asyncio (api), Next.js 15 server actions + Vitest (web).

**Spec:** `docs/superpowers/specs/2026-07-16-auto-login-pos-pagamento-design.md`

## Global Constraints

- Token: `secrets.token_urlsafe(32)`. Chaves Redis: `signup:handoff:{session_id}` (token em claro) e `signup:token:{sha256(token) hexdigest}` (str do `user_id`), ambas `ex=900`.
- Falha de Redis na geração: `logger.warning` e segue — NUNCA falha o webhook.
- Recompra (`_process_recompra`) NÃO gera token.
- `signup-login` com token inválido/expirado/reusado → **401** com detail "Token inválido ou expirado" (genérico, sem distinguir); tenant suspenso → 403 (reusa `_validar_tenant_ativo`).
- Web: `redirect("/inicio")` na action fica FORA do try/catch (o redirect do Next lança `NEXT_REDIRECT` por design — um catch genérico o engoliria).
- Fallback sempre preservado: sem `login_token` (ou action falhou) → botão "Ir para o login" atual.
- Comandos: api → `cd apps/api && uv run pytest tests/unit -q` + `uv run ruff check . && uv run ruff format --check .`; web → `cd apps/web && pnpm test` + `pnpm lint`.

---

### Task 1: service de tokens + geração no webhook + entrega no status

**Files:**
- Create: `apps/api/app/services/signup_tokens.py`
- Modify: `apps/api/app/services/billing.py` (fim do `_process_signup`), `apps/api/app/api/v1/signup.py` (status), `apps/api/app/schemas/signup.py` (`SignupStatusOut.login_token`)
- Test: `apps/api/tests/unit/test_signup_tokens.py` (novo), `apps/api/tests/unit/test_billing_service.py`, `apps/api/tests/unit/test_signup_routes.py`

**Interfaces:**
- Consumes: `get_redis()` (`app/core/redis.py`, singleton async).
- Produces: `store_login_token(redis, session_id: str, user_id) -> None`; `claim_handoff_token(redis, session_id: str) -> str | None`; `consume_login_token(redis, token: str) -> str | None` (retorna `user_id` como str). Task 2 consome `consume_login_token`; o front consome `SignupStatusOut{ready, login_token}`.

- [ ] **Step 1: Testes do service (falhando)**

Criar `apps/api/tests/unit/test_signup_tokens.py`:

```python
import hashlib
from unittest.mock import AsyncMock

from app.services.signup_tokens import (
    claim_handoff_token,
    consume_login_token,
    store_login_token,
)


async def test_store_grava_as_duas_chaves_com_ttl() -> None:
    redis = AsyncMock()

    await store_login_token(redis, "cs_test_123", "user-uuid")

    assert redis.set.await_count == 2
    calls = {call.args[0]: call for call in redis.set.await_args_list}
    handoff_call = calls["signup:handoff:cs_test_123"]
    token = handoff_call.args[1]
    assert handoff_call.kwargs["ex"] == 900

    sha = hashlib.sha256(token.encode()).hexdigest()
    token_call = calls[f"signup:token:{sha}"]
    assert token_call.args[1] == "user-uuid"
    assert token_call.kwargs["ex"] == 900


async def test_claim_faz_getdel_do_handoff() -> None:
    redis = AsyncMock()
    redis.getdel.return_value = "token-em-claro"

    result = await claim_handoff_token(redis, "cs_test_123")

    assert result == "token-em-claro"
    redis.getdel.assert_awaited_once_with("signup:handoff:cs_test_123")


async def test_consume_faz_getdel_pelo_hash() -> None:
    redis = AsyncMock()
    redis.getdel.return_value = "user-uuid"

    result = await consume_login_token(redis, "meu-token")

    sha = hashlib.sha256(b"meu-token").hexdigest()
    redis.getdel.assert_awaited_once_with(f"signup:token:{sha}")
    assert result == "user-uuid"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_signup_tokens.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implementar o service**

Criar `apps/api/app/services/signup_tokens.py`:

```python
"""Token one-time de auto-login pós-cadastro.

Duas chaves no Redis, ambas de uso único (GETDEL) e TTL curto:
- signup:handoff:{session_id} → token em claro. Entregue UMA vez pelo
  GET /signup/status — o navegador legítimo está pollando desde antes da
  conta existir, então sempre chega primeiro; a URL com session_id vazada
  depois não destrava mais nada.
- signup:token:{sha256(token)} → user_id. Trocado UMA vez por par JWT no
  POST /auth/signup-login — em repouso só o hash.
"""

import hashlib
import secrets

from redis.asyncio import Redis

TOKEN_TTL_SECONDS = 900
_HANDOFF_PREFIX = "signup:handoff:"
_TOKEN_PREFIX = "signup:token:"


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def store_login_token(redis: Redis, session_id: str, user_id) -> None:
    token = secrets.token_urlsafe(32)
    await redis.set(f"{_HANDOFF_PREFIX}{session_id}", token, ex=TOKEN_TTL_SECONDS)
    await redis.set(f"{_TOKEN_PREFIX}{_sha256(token)}", str(user_id), ex=TOKEN_TTL_SECONDS)


async def claim_handoff_token(redis: Redis, session_id: str) -> str | None:
    return await redis.getdel(f"{_HANDOFF_PREFIX}{session_id}")


async def consume_login_token(redis: Redis, token: str) -> str | None:
    return await redis.getdel(f"{_TOKEN_PREFIX}{_sha256(token)}")
```

Run: `cd apps/api && uv run pytest tests/unit/test_signup_tokens.py -q`
Expected: PASS (3 testes).

- [ ] **Step 4: Testes da geração no webhook (falhando)**

Em `apps/api/tests/unit/test_billing_service.py`, na classe que testa `_process_signup`/`process_checkout_completed` (ver `test_cria_tenant_user_e_transacao`), adicionar — adaptando aos fixtures/mocks reais do arquivo:

```python
    async def test_signup_gera_token_de_auto_login(self, session, monkeypatch) -> None:
        redis = AsyncMock()
        monkeypatch.setattr(billing_module, "get_redis", AsyncMock(return_value=redis))
        store_mock = AsyncMock()
        monkeypatch.setattr(billing_module, "store_login_token", store_mock)
        # ... mesmo setup do test_cria_tenant_user_e_transacao ...

        await process_checkout_completed(session, stripe_session)

        store_mock.assert_awaited_once()
        assert store_mock.await_args.args[1] == stripe_session["id"]

    async def test_falha_no_redis_nao_quebra_o_webhook(self, session, monkeypatch) -> None:
        monkeypatch.setattr(
            billing_module, "get_redis", AsyncMock(side_effect=RuntimeError("redis fora"))
        )
        # ... mesmo setup ...

        await process_checkout_completed(session, stripe_session)  # não levanta

    async def test_recompra_nao_gera_token(self, session, monkeypatch) -> None:
        store_mock = AsyncMock()
        monkeypatch.setattr(billing_module, "store_login_token", store_mock)
        # ... setup de recompra (ver test_credita_tenant_existente_sem_criar_user) ...

        await process_checkout_completed(session, stripe_session_recompra)

        store_mock.assert_not_awaited()
```

(`billing_module` = `import app.services.billing as billing_module` — adicionar se ausente.)

- [ ] **Step 5: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_billing_service.py -q`
Expected: FAIL — `AttributeError: get_redis`/`store_login_token` no monkeypatch.

- [ ] **Step 6: Gerar o token no `_process_signup`**

Em `apps/api/app/services/billing.py`: adicionar aos imports

```python
from app.core.redis import get_redis
from app.services.signup_tokens import store_login_token
```

e, em `_process_signup`, LOGO APÓS o commit bem-sucedido que cria tenant+user+transação (depois do bloco existente de commit/tratamento de IntegrityError, no caminho de sucesso — ler a função pra achar o ponto exato; o `user` criado precisa estar acessível):

```python
    # Auto-login pós-pagamento: token one-time, best-effort — se o Redis
    # falhar, a conta já existe e o usuário entra pelo /login normal.
    try:
        redis = await get_redis()
        await store_login_token(redis, session_id, user.id)
    except Exception as exc:
        logger.warning(
            "Falha ao gravar token de auto-login | session=%s erro=%s", session_id, exc
        )
```

(Se a variável do usuário criado tiver outro nome no código real, usar o nome real; se o `user.id` só existir após flush/commit, confirmar que o ponto de inserção é pós-commit — `expire_on_commit=False` mantém o atributo acessível.)

- [ ] **Step 7: Status entrega o token (teste + implementação)**

Em `apps/api/app/schemas/signup.py`:

```python
class SignupStatusOut(BaseModel):
    ready: bool
    login_token: str | None = None
```

Em `apps/api/tests/unit/test_signup_routes.py`, adicionar (adaptando aos fixtures reais — o arquivo já testa o status; seguir o desenho):

```python
    def test_status_ready_entrega_login_token_uma_vez(self, client, session, monkeypatch) -> None:
        redis = AsyncMock()
        redis.getdel.return_value = "token-one-time"
        monkeypatch.setattr(signup_module, "get_redis", AsyncMock(return_value=redis))
        session.scalar.return_value = uuid.uuid4()  # transação encontrada → ready

        response = client.get("/api/v1/signup/status?session_id=cs_123")

        assert response.status_code == 200
        assert response.json() == {"ready": True, "login_token": "token-one-time"}
        redis.getdel.assert_awaited_once_with("signup:handoff:cs_123")

    def test_status_nao_ready_nao_toca_no_redis(self, client, session, monkeypatch) -> None:
        redis = AsyncMock()
        monkeypatch.setattr(signup_module, "get_redis", AsyncMock(return_value=redis))
        session.scalar.return_value = None

        response = client.get("/api/v1/signup/status?session_id=cs_123")

        assert response.json() == {"ready": False, "login_token": None}
        redis.getdel.assert_not_awaited()
```

(`signup_module` = `import app.api.v1.signup as signup_module`.)

Implementação em `apps/api/app/api/v1/signup.py` — substituir `signup_status`:

```python
@router.get("/status")
async def signup_status(
    session_id: str = Query(...),
    session: AsyncSession = Depends(get_system_session),
) -> SignupStatusOut:
    found = await session.scalar(
        select(CreditTransaction.id).where(CreditTransaction.stripe_payment_id == session_id)
    )
    if found is None:
        return SignupStatusOut(ready=False)

    # Entrega única (GETDEL): o primeiro polling após a conta ficar pronta
    # leva o token; chamadas seguintes (ou URL vazada depois) recebem null.
    login_token: str | None = None
    try:
        redis = await get_redis()
        login_token = await claim_handoff_token(redis, session_id)
    except Exception:
        logger.warning("Falha ao buscar token de auto-login | session=%s", session_id)
    return SignupStatusOut(ready=True, login_token=login_token)
```

Imports novos no arquivo: `import logging`, `from app.core.redis import get_redis`, `from app.services.signup_tokens import claim_handoff_token`, e `logger = logging.getLogger(__name__)`.

- [ ] **Step 8: Rodar tudo + lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS e lint limpo.

- [ ] **Step 9: Commit**

```bash
git add apps/api/app/services/signup_tokens.py apps/api/app/services/billing.py apps/api/app/api/v1/signup.py apps/api/app/schemas/signup.py apps/api/tests/unit/test_signup_tokens.py apps/api/tests/unit/test_billing_service.py apps/api/tests/unit/test_signup_routes.py
git commit -m "feat(api): token one-time de auto-login gerado no webhook e entregue no signup/status"
```

---

### Task 2: `POST /auth/signup-login` troca o token por JWT

**Files:**
- Modify: `apps/api/app/services/auth.py` (função nova), `apps/api/app/api/v1/auth.py` (rota nova), `apps/api/app/schemas/auth.py` (`SignupLoginRequest`)
- Test: `apps/api/tests/unit/test_auth_routes.py`

**Interfaces:**
- Consumes: `consume_login_token(redis, token) -> str | None` (Task 1); `create_access_token`/`create_refresh_token`/`_validar_tenant_ativo` (existem em `services/auth.py`); `get_redis` (dependency já usada nas rotas de refresh/logout).
- Produces: `POST /api/v1/auth/signup-login` body `{token}` → `TokenPair`. Task 3 consome.

- [ ] **Step 1: Testes (falhando)**

Em `apps/api/tests/unit/test_auth_routes.py`, adicionar (adaptando aos fixtures/overrides reais do arquivo — ele já testa login/refresh com Redis mockado; seguir o desenho):

```python
class TestSignupLogin:
    def test_token_valido_retorna_par_de_tokens(self, client, session, monkeypatch) -> None:
        user = _user()  # factory existente do arquivo (ou equivalente)
        session.get.return_value = user
        consume = AsyncMock(return_value=str(user.id))
        monkeypatch.setattr(auth_service_module, "consume_login_token", consume)
        monkeypatch.setattr(
            auth_service_module, "_validar_tenant_ativo", AsyncMock(return_value=None)
        )

        response = client.post("/api/v1/auth/signup-login", json={"token": "tok-valido"})

        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body and "refresh_token" in body

    def test_token_invalido_retorna_401(self, client, session, monkeypatch) -> None:
        monkeypatch.setattr(
            auth_service_module, "consume_login_token", AsyncMock(return_value=None)
        )

        response = client.post("/api/v1/auth/signup-login", json={"token": "tok-ruim"})

        assert response.status_code == 401
        assert response.json()["detail"] == "Token inválido ou expirado"

    def test_user_sumiu_retorna_401_generico(self, client, session, monkeypatch) -> None:
        monkeypatch.setattr(
            auth_service_module,
            "consume_login_token",
            AsyncMock(return_value=str(uuid.uuid4())),
        )
        session.get.return_value = None

        response = client.post("/api/v1/auth/signup-login", json={"token": "tok"})

        assert response.status_code == 401
```

(`auth_service_module` = `import app.services.auth as auth_service_module`.)

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_auth_routes.py -q`
Expected: FAIL — 404 na rota nova / `AttributeError` no monkeypatch.

- [ ] **Step 3: Implementar**

`apps/api/app/schemas/auth.py` — ao final:

```python
class SignupLoginRequest(BaseModel):
    token: str = Field(min_length=1)
```

(Se `Field` não estiver importado no arquivo, adicionar.)

`apps/api/app/services/auth.py` — imports novos (`import uuid` se ausente; `from app.services.signup_tokens import consume_login_token`) e função nova após `login`:

```python
_TOKEN_INVALIDO = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido ou expirado"
)


async def signup_token_login(token: str, session: AsyncSession, redis: Redis) -> tuple[str, str]:
    """Troca o token one-time do cadastro por um par de JWT (uso único).

    401 genérico pra token inválido/expirado/reusado e pra user inexistente —
    sem oráculo de qual caso ocorreu.
    """
    user_id = await consume_login_token(redis, token)
    if user_id is None:
        raise _TOKEN_INVALIDO

    user = await session.get(User, uuid.UUID(user_id))
    if user is None:
        raise _TOKEN_INVALIDO

    await _validar_tenant_ativo(user, session)

    logger.info("Auto-login pós-cadastro | user=%s tenant=%s", user.id, user.tenant_id)
    return (
        create_access_token(str(user.id), str(user.tenant_id), user.role),
        create_refresh_token(str(user.id)),
    )
```

`apps/api/app/api/v1/auth.py` — rota nova após `login` (import `SignupLoginRequest` no import de schemas):

```python
@router.post("/signup-login")
async def signup_login(
    body: SignupLoginRequest,
    session: AsyncSession = Depends(get_system_session),
    redis: Redis = Depends(get_redis),
) -> TokenPair:
    access_token, refresh_token = await auth_service.signup_token_login(
        body.token, session, redis
    )
    return TokenPair(access_token=access_token, refresh_token=refresh_token)
```

(Conferir que `Redis`/`get_redis` já estão importados no arquivo — as rotas de refresh/logout usam.)

- [ ] **Step 4: Rodar tudo + lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS e lint limpo.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/auth.py apps/api/app/api/v1/auth.py apps/api/app/schemas/auth.py apps/api/tests/unit/test_auth_routes.py
git commit -m "feat(api): POST /auth/signup-login troca o token one-time por par de JWT"
```

---

### Task 3: web — server action + painel + CLAUDE.md

**Files:**
- Create: `apps/web/src/app/cadastro/actions.ts`
- Modify: `apps/web/src/components/SignupSuccessPanel.tsx`, `CLAUDE.md`
- Test: `apps/web/__tests__/SignupSuccessPanel.test.tsx`

**Interfaces:**
- Consumes: `POST /api/v1/auth/signup-login` → `TokenPair` (Task 2); `setAuthCookies` (`@/lib/auth`), `API_URL` (`@/lib/backend`); resposta do status `{ready, login_token}` (Task 1).

- [ ] **Step 1: Testes (falhando)**

Em `apps/web/__tests__/SignupSuccessPanel.test.tsx` — o arquivo existe; adicionar o mock da action no topo (junto do mock de `client-api` existente) e os testes (adaptando aos helpers do arquivo):

```tsx
vi.mock("@/app/cadastro/actions", () => ({
  autoLogin: vi.fn(),
}));
```

```tsx
  it("chama autoLogin quando o status traz login_token", async () => {
    const { autoLogin } = await import("@/app/cadastro/actions");
    vi.mocked(autoLogin).mockResolvedValue({ error: null });
    backendFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ ready: true, login_token: "tok-1" }),
    });

    render(<SignupSuccessPanel sessionId="cs_123" />);

    await waitFor(() => expect(autoLogin).toHaveBeenCalledWith("tok-1"));
    expect(screen.getByText(/Entrando/)).toBeInTheDocument();
  });

  it("sem login_token mantém o botão de ir para o login", async () => {
    backendFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ ready: true, login_token: null }),
    });

    render(<SignupSuccessPanel sessionId="cs_123" />);

    await waitFor(() => expect(screen.getByText("Ir para o login")).toBeInTheDocument());
  });

  it("erro na action cai no fallback com o botão de login", async () => {
    const { autoLogin } = await import("@/app/cadastro/actions");
    vi.mocked(autoLogin).mockResolvedValue({ error: "invalid" });
    backendFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ ready: true, login_token: "tok-1" }),
    });

    render(<SignupSuccessPanel sessionId="cs_123" />);

    await waitFor(() => expect(screen.getByText("Ir para o login")).toBeInTheDocument());
  });
```

(Conferir o nome real do mock de `backendFetch` no arquivo — seguir o existente.)

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/web && pnpm test -- SignupSuccessPanel`
Expected: FAIL — módulo `@/app/cadastro/actions` não existe.

- [ ] **Step 3: Server action**

Criar `apps/web/src/app/cadastro/actions.ts`:

```ts
"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { setAuthCookies } from "@/lib/auth";
import { API_URL } from "@/lib/backend";

export async function autoLogin(token: string): Promise<{ error: string | null }> {
  let tokens: { access_token: string; refresh_token: string };
  try {
    const response = await fetch(`${API_URL}/api/v1/auth/signup-login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token }),
      cache: "no-store",
    });
    if (!response.ok) {
      return { error: "invalid" };
    }
    tokens = await response.json();
  } catch {
    return { error: "network" };
  }

  setAuthCookies(await cookies(), tokens);
  // Fora do try/catch: o redirect do Next lança NEXT_REDIRECT por design.
  redirect("/inicio");
}
```

- [ ] **Step 4: Painel**

Em `apps/web/src/components/SignupSuccessPanel.tsx` — substituir o componente por:

```tsx
"use client";

import { useEffect, useState } from "react";

import { autoLogin } from "@/app/cadastro/actions";
import { backendFetch } from "@/lib/client-api";

const MAX_ATTEMPTS = 8;

export function SignupSuccessPanel({
  sessionId,
  pollMs = 2000,
}: {
  sessionId: string | null;
  pollMs?: number;
}) {
  const [ready, setReady] = useState(false);
  const [attempts, setAttempts] = useState(0);
  const [loggingIn, setLoggingIn] = useState(false);

  async function tryAutoLogin(token: string) {
    setLoggingIn(true);
    try {
      const result = await autoLogin(token);
      if (result?.error) {
        // Token rejeitado (expirado/reusado): volta pro fallback com o botão.
        setLoggingIn(false);
      }
      // Sem erro: a action redirecionou pro /inicio — o Next cuida da
      // navegação e este componente sai de cena.
    } catch {
      // Rejeição inesperada da action (ex: rede): volta pro fallback.
      setLoggingIn(false);
    }
  }

  async function checkStatus() {
    if (!sessionId) return;
    try {
      const response = await backendFetch(
        `signup/status?session_id=${encodeURIComponent(sessionId)}`,
      );
      if (response.ok) {
        const body = await response.json();
        if (body.ready) {
          setReady(true);
          if (body.login_token) {
            void tryAutoLogin(body.login_token);
          }
          return;
        }
      }
    } catch {
      // Rede instável durante o polling — só tenta de novo no próximo ciclo.
    }
    setAttempts((prev) => prev + 1);
  }

  useEffect(() => {
    void checkStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId || ready || attempts >= MAX_ATTEMPTS) return;
    const interval = setInterval(() => void checkStatus(), pollMs);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, ready, attempts, pollMs]);

  const settled = ready || attempts >= MAX_ATTEMPTS || !sessionId;

  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm text-center">
        <h1 className="font-display text-3xl font-semibold text-ink">
          {settled ? "Pagamento confirmado" : "Confirmando seu pagamento…"}
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          {loggingIn
            ? "Entrando na sua conta…"
            : settled
              ? "Sua conta está pronta. Você já pode entrar com o e-mail e a senha que cadastrou."
              : "Isso leva só alguns segundos."}
        </p>
        {settled && !loggingIn && (
          <a
            href="/login"
            className="mt-6 inline-block rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink"
          >
            Ir para o login
          </a>
        )}
      </div>
    </main>
  );
}
```

- [ ] **Step 5: Rodar tudo + lint**

Run: `cd apps/web && pnpm test && pnpm lint`
Expected: PASS (novos + pré-existentes do painel) e lint sem erros novos.

- [ ] **Step 6: CLAUDE.md**

Localizar (seção Frontend, bullet da página `/`):

```
`/cadastro/sucesso` faz polling em `GET /api/v1/signup/status` até a conta ficar pronta (nunca mostra erro, mesmo em timeout — o pagamento já foi aprovado pela Stripe nesse ponto) e linka pro `/login`; `/cadastro/cancelado` é estática.
```

Substituir por:

```
`/cadastro/sucesso` faz polling em `GET /api/v1/signup/status` até a conta ficar pronta (nunca mostra erro, mesmo em timeout — o pagamento já foi aprovado pela Stripe nesse ponto) e **loga sozinho**: o status entrega um `login_token` one-time (gerado no webhook, Redis `signup:handoff:{session_id}`/`signup:token:{sha256}`, TTL 900s, GETDEL nos dois — o `session_id` da URL nunca vira credencial), trocado por par JWT em `POST /api/v1/auth/signup-login` via server action que seta os cookies e redireciona pro `/inicio`; sem token (expirado/já usado), cai no fallback com o botão pro `/login`. `/cadastro/cancelado` é estática.
```

Se o trecho não bater verbatim, PARAR e reportar.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/app/cadastro/actions.ts apps/web/src/components/SignupSuccessPanel.tsx apps/web/__tests__/SignupSuccessPanel.test.tsx CLAUDE.md
git commit -m "feat(web): auto-login pós-pagamento — /cadastro/sucesso entra direto no /inicio"
```

---

## Nota pós-deploy (manual, fora do código)

Nada — Redis já está no stack, nenhuma env nova, nenhuma migration.
