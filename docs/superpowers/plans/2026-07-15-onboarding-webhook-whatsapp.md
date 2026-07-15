# Onboarding do WhatsApp (subscribed_apps + instruções de webhook) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar os dois buracos do onboarding manual do WhatsApp: inscrever o app do tenant na WABA automaticamente no connect, e exibir as instruções do passo manual de webhook na página `/configuracoes/whatsapp`.

**Architecture:** (1) Nova função `subscribe_app_to_waba` no client Graph API existente, chamada no `POST /whatsapp/connect` entre `register_number` e a persistência — falha bloqueia o connect (400/502), mantendo a garantia "nada é salvo se a Meta rejeitar". (2) Novo endpoint `GET /whatsapp/webhook-config` devolve `callback_url` + `verify_token` lidos do env do api (nova env `API_PUBLIC_URL`); o `WhatsAppConnectionPanel` busca esse endpoint e renderiza uma seção de instruções com botões de copiar.

**Tech Stack:** FastAPI + httpx (api), Next.js 15 + Vitest/Testing Library (web).

**Spec:** `docs/superpowers/specs/2026-07-15-onboarding-webhook-whatsapp-design.md`

## Global Constraints

- Falha em `subscribe_app_to_waba` bloqueia o connect: `WhatsAppApiError` → 400 com a mensagem da Meta; `WhatsAppNetworkError` → 502 com `_GRAPH_ERROR_DETAIL`. Nada persiste.
- Fallback de erro da inscrição: "Não foi possível inscrever o app na WhatsApp Business Account — confira o WABA ID".
- `API_PUBLIC_URL` vazia (default) → `callback_url` degrada pro path relativo `/api/v1/webhooks/whatsapp`.
- A seção de instruções no front só renderiza quando o endpoint respondeu com `callback_url` e `verify_token` presentes — nunca quebra o resto da página.
- Testes web mockam `backendFetch` (o padrão do arquivo de teste existente), não msw.
- Comandos de teste: `uv run pytest tests/unit -q` (dentro de `apps/api`), `pnpm test` (dentro de `apps/web`). Lint: `uv run ruff check . && uv run ruff format --check .` / `pnpm lint`.

---

### Task 1: `subscribe_app_to_waba` no client + chamada no connect

**Files:**
- Modify: `apps/api/app/clients/whatsapp.py` (adicionar função ao final)
- Modify: `apps/api/app/api/v1/whatsapp.py:16-22` (import) e `:65-71` (novo bloco após register)
- Test: `apps/api/tests/unit/test_whatsapp_connection_routes.py`

**Interfaces:**
- Consumes: `settings.graph_api_base_url`, `settings.graph_api_version`, `_meta_error_message`, exceções `WhatsAppApiError`/`WhatsAppNetworkError` (todas já existem em `clients/whatsapp.py`).
- Produces: `async def subscribe_app_to_waba(waba_id: str, access_token: str) -> None` — usada só pelo route `connect`.

- [ ] **Step 1: Atualizar o fixture `graph_mocks` e escrever os testes que falham**

Em `apps/api/tests/unit/test_whatsapp_connection_routes.py`, atualizar o fixture `graph_mocks` (linhas 64-74) para incluir o mock da inscrição:

```python
@pytest.fixture
def graph_mocks(monkeypatch):
    mocks = {
        "fetch": AsyncMock(return_value="+5511987654321"),
        "register": AsyncMock(return_value=None),
        "subscribe": AsyncMock(return_value=None),
        "encrypt": MagicMock(return_value="token-cifrado"),
    }
    monkeypatch.setattr(whatsapp_module, "fetch_display_phone_number", mocks["fetch"])
    monkeypatch.setattr(whatsapp_module, "register_number", mocks["register"])
    monkeypatch.setattr(whatsapp_module, "subscribe_app_to_waba", mocks["subscribe"])
    monkeypatch.setattr(whatsapp_module, "encrypt_access_token", mocks["encrypt"])
    return mocks
```

No teste `test_conexao_feliz_nova` (dentro de `class TestConnect`), adicionar ao final:

```python
        graph_mocks["subscribe"].assert_awaited_once_with("WABA", "token-claro")
```

Adicionar dois testes novos dentro de `class TestConnect`:

```python
    def test_falha_no_subscribe_retorna_400_sem_persistir(
        self, client, session, graph_mocks
    ) -> None:
        graph_mocks["subscribe"].side_effect = WhatsAppApiError("WABA não encontrada")

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 400
        assert response.json()["detail"] == "WABA não encontrada"
        session.commit.assert_not_awaited()

    def test_falha_de_rede_no_subscribe_retorna_502_sem_persistir(
        self, client, session, graph_mocks
    ) -> None:
        graph_mocks["subscribe"].side_effect = WhatsAppNetworkError("timeout")

        response = client.post("/api/v1/whatsapp/connect", json=CONNECT_BODY)

        assert response.status_code == 502
        session.commit.assert_not_awaited()
```

- [ ] **Step 2: Rodar os testes e ver falharem**

Run: `cd apps/api && uv run pytest tests/unit/test_whatsapp_connection_routes.py -q`
Expected: FAIL — `AttributeError: <module 'app.api.v1.whatsapp'> does not have the attribute 'subscribe_app_to_waba'` (o monkeypatch do fixture falha antes de qualquer teste do grupo rodar).

- [ ] **Step 3: Implementar a função no client**

Ao final de `apps/api/app/clients/whatsapp.py`:

```python
async def subscribe_app_to_waba(waba_id: str, access_token: str) -> None:
    """Inscreve o app do tenant na WABA — sem isso a Meta não entrega webhook de mensagem."""
    url = f"{settings.graph_api_base_url}/{settings.graph_api_version}/{waba_id}/subscribed_apps"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError as exc:
        raise WhatsAppNetworkError(f"Falha de rede ao inscrever app na WABA: {exc}") from exc

    if response.is_error:
        logger.warning(
            "Graph API (subscribed_apps) retornou erro | status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise WhatsAppApiError(
            _meta_error_message(
                response,
                "Não foi possível inscrever o app na WhatsApp Business Account — "
                "confira o WABA ID",
            )
        )
```

- [ ] **Step 4: Chamar no route `connect`**

Em `apps/api/app/api/v1/whatsapp.py`, atualizar o import (linhas 17-22):

```python
from app.clients.whatsapp import (
    WhatsAppApiError,
    WhatsAppNetworkError,
    fetch_display_phone_number,
    register_number,
    subscribe_app_to_waba,
)
```

E inserir, logo após o bloco `try`/`except` do `register_number` (após a linha 71, antes de `existing = await session.scalar(...)`):

```python
    try:
        await subscribe_app_to_waba(body.waba_id, body.access_token)
    except WhatsAppNetworkError as exc:
        logger.error("Falha de rede ao inscrever app na WABA | erro=%s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_GRAPH_ERROR_DETAIL)
    except WhatsAppApiError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
```

- [ ] **Step 5: Rodar os testes e ver passarem**

Run: `cd apps/api && uv run pytest tests/unit/test_whatsapp_connection_routes.py -q`
Expected: PASS (todos, incluindo os 2 novos).

- [ ] **Step 6: Lint e suíte completa**

Run: `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run pytest tests/unit -q`
Expected: sem erros; 244+ testes passando.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/clients/whatsapp.py apps/api/app/api/v1/whatsapp.py apps/api/tests/unit/test_whatsapp_connection_routes.py
git commit -m "feat(api): inscreve o app do tenant na WABA automaticamente no connect"
```

---

### Task 2: endpoint `GET /whatsapp/webhook-config`

**Files:**
- Modify: `apps/api/app/core/config.py` (nova setting, junto das meta_* na linha ~27)
- Modify: `apps/api/app/schemas/whatsapp_connection.py` (novo schema ao final)
- Modify: `apps/api/app/api/v1/whatsapp.py` (novo route após `get_connection`)
- Test: `apps/api/tests/unit/test_whatsapp_connection_routes.py`

**Interfaces:**
- Consumes: `settings.meta_verify_token` (existe), `get_current_tenant` (existe).
- Produces: setting `api_public_url: str = ""` (env `API_PUBLIC_URL`); schema `WebhookConfigOut(callback_url: str, verify_token: str)`; route `GET /api/v1/whatsapp/webhook-config` → `WebhookConfigOut`. O front (Task 3) consome esse contrato.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `apps/api/tests/unit/test_whatsapp_connection_routes.py`:

```python
class TestWebhookConfig:
    def test_sem_token_retorna_401(self) -> None:
        response = TestClient(app).get("/api/v1/whatsapp/webhook-config")
        assert response.status_code == 401

    def test_retorna_url_completa_e_verify_token(self, client, monkeypatch) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "api_public_url", "https://api.exemplo.com.br")
        monkeypatch.setattr(settings, "meta_verify_token", "meu-verify-token")

        response = client.get("/api/v1/whatsapp/webhook-config")

        assert response.status_code == 200
        assert response.json() == {
            "callback_url": "https://api.exemplo.com.br/api/v1/webhooks/whatsapp",
            "verify_token": "meu-verify-token",
        }

    def test_sem_api_public_url_degrada_pra_path_relativo(self, client, monkeypatch) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "api_public_url", "")
        monkeypatch.setattr(settings, "meta_verify_token", "meu-verify-token")

        response = client.get("/api/v1/whatsapp/webhook-config")

        assert response.status_code == 200
        assert response.json()["callback_url"] == "/api/v1/webhooks/whatsapp"
```

- [ ] **Step 2: Rodar e ver falharem**

Run: `cd apps/api && uv run pytest tests/unit/test_whatsapp_connection_routes.py::TestWebhookConfig -q`
Expected: FAIL — 404 nos dois testes autenticados (rota não existe) e `AttributeError: api_public_url` no monkeypatch.

- [ ] **Step 3: Implementar**

Em `apps/api/app/core/config.py`, logo abaixo de `meta_verify_token` (linha 27):

```python
    # URL pública do api (ex: https://api.rootlab.com.br) — usada pra montar a
    # callback_url do webhook exibida na página de setup do WhatsApp.
    api_public_url: str = ""
```

Ao final de `apps/api/app/schemas/whatsapp_connection.py`:

```python
class WebhookConfigOut(BaseModel):
    callback_url: str
    verify_token: str
```

Em `apps/api/app/api/v1/whatsapp.py`: adicionar `WebhookConfigOut` ao import de schemas
(`from app.schemas.whatsapp_connection import ConnectWhatsAppRequest, WebhookConfigOut, WhatsAppConnectionOut`),
adicionar `from app.core.config import settings` aos imports, e o route após `get_connection`:

```python
@router.get("/webhook-config")
async def get_webhook_config(
    ctx: TenantContext = Depends(get_current_tenant),
) -> WebhookConfigOut:
    """Valores que o escritório precisa colar no painel da Meta (passo manual do webhook).

    Só leitura de config — não toca em tabela nenhuma, então não usa
    get_tenant_session; a autenticação de tenant continua obrigatória.
    """
    base = settings.api_public_url.rstrip("/")
    return WebhookConfigOut(
        callback_url=f"{base}/api/v1/webhooks/whatsapp",
        verify_token=settings.meta_verify_token,
    )
```

- [ ] **Step 4: Rodar e ver passarem**

Run: `cd apps/api && uv run pytest tests/unit/test_whatsapp_connection_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Lint e suíte completa**

Run: `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run pytest tests/unit -q`
Expected: sem erros.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/core/config.py apps/api/app/schemas/whatsapp_connection.py apps/api/app/api/v1/whatsapp.py apps/api/tests/unit/test_whatsapp_connection_routes.py
git commit -m "feat(api): GET /whatsapp/webhook-config expõe callback_url + verify token pro setup"
```

---

### Task 3: seção de instruções no `WhatsAppConnectionPanel` + CLAUDE.md

**Files:**
- Modify: `apps/web/src/components/WhatsAppConnectionPanel.tsx`
- Modify: `CLAUDE.md` (seção "Integração WhatsApp Business")
- Test: `apps/web/__tests__/WhatsAppConnectionPanel.test.tsx`

**Interfaces:**
- Consumes: `GET whatsapp/webhook-config` → `{callback_url: string, verify_token: string}` (Task 2), `backendFetch` (existe).
- Produces: nada consumido por outras tasks.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final do `describe` em `apps/web/__tests__/WhatsAppConnectionPanel.test.tsx`:

```tsx
  it("mostra as instruções de webhook com os valores do endpoint e copia a URL", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    mockedBackendFetch.mockImplementation(async (path: string) => {
      if (path === "whatsapp/webhook-config") {
        return {
          ok: true,
          json: async () => ({
            callback_url: "https://api.exemplo.com.br/api/v1/webhooks/whatsapp",
            verify_token: "meu-verify-token",
          }),
        };
      }
      return { ok: true, json: async () => null };
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() =>
      expect(screen.getByText("Configurar webhook na Meta")).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Callback URL")).toHaveValue(
      "https://api.exemplo.com.br/api/v1/webhooks/whatsapp",
    );
    expect(screen.getByLabelText("Verify token")).toHaveValue("meu-verify-token");

    fireEvent.click(screen.getAllByRole("button", { name: "Copiar" })[0]);

    await waitFor(() => expect(screen.getByText("Copiado!")).toBeInTheDocument());
    expect(writeText).toHaveBeenCalledWith(
      "https://api.exemplo.com.br/api/v1/webhooks/whatsapp",
    );
  });

  it("não mostra a seção de webhook quando o endpoint falha", async () => {
    mockedBackendFetch.mockImplementation(async (path: string) => {
      if (path === "whatsapp/webhook-config") {
        return { ok: false, json: async () => null };
      }
      return { ok: true, json: async () => null };
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByText("Phone Number ID")).toBeInTheDocument());
    expect(screen.queryByText("Configurar webhook na Meta")).not.toBeInTheDocument();
  });
```

- [ ] **Step 2: Rodar e ver falharem**

Run: `cd apps/web && pnpm test -- WhatsAppConnectionPanel`
Expected: FAIL — "Configurar webhook na Meta" não encontrado.

- [ ] **Step 3: Implementar no componente**

Em `apps/web/src/components/WhatsAppConnectionPanel.tsx`:

Adicionar o tipo e estados (junto dos existentes, após a linha 47):

```tsx
type WebhookConfig = { callback_url: string; verify_token: string };
```

```tsx
  const [webhookConfig, setWebhookConfig] = useState<WebhookConfig | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
```

Atualizar `load()` para buscar também a config (substituir a função inteira):

```tsx
  async function load() {
    try {
      const response = await backendFetch("whatsapp/connection");
      if (response.ok) {
        setConnection(await response.json());
      }
      const configResponse = await backendFetch("whatsapp/webhook-config");
      if (configResponse.ok) {
        const config = await configResponse.json().catch(() => null);
        if (config?.callback_url && config?.verify_token) {
          setWebhookConfig(config);
        }
      }
    } finally {
      setLoaded(true);
    }
  }
```

Adicionar o handler de cópia (após `handleDisconnect`):

```tsx
  async function handleCopy(field: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(field);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      // clipboard indisponível (http/permissão) — sem feedback, sem quebrar
    }
  }
```

Renderizar a seção ao final do `<div className="flex-1 overflow-y-auto px-8 py-6">`, logo após o bloco condicional `connection && !showForm ? (...) : (...)` (irmã dele, dentro do mesmo div):

```tsx
        {webhookConfig && (
          <section className="mt-8 max-w-xl rounded border border-line bg-surface p-6">
            <h2 className="font-display text-base font-semibold text-ink">
              Configurar webhook na Meta
            </h2>
            <p className="mt-1 text-sm text-muted">
              Passo obrigatório: sem o webhook, as mensagens enviadas ao número não chegam à
              plataforma.
            </p>
            <ol className="mt-4 flex list-decimal flex-col gap-3 pl-5 text-sm text-ink">
              <li>
                No painel do seu app em developers.facebook.com, abra{" "}
                <span className="font-medium">WhatsApp → Configuration → Webhook</span> e clique
                em Edit.
              </li>
              <li>
                Preencha com os valores abaixo e clique em Verify and save:
                <div className="mt-2 flex flex-col gap-2">
                  <div className="flex items-center gap-2">
                    <input
                      readOnly
                      aria-label="Callback URL"
                      value={webhookConfig.callback_url}
                      className="flex-1 rounded border border-line bg-ground px-3 py-2 font-mono text-xs text-ink"
                    />
                    <button
                      type="button"
                      onClick={() => void handleCopy("url", webhookConfig.callback_url)}
                      className="rounded border border-line px-3 py-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                    >
                      {copied === "url" ? "Copiado!" : "Copiar"}
                    </button>
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      readOnly
                      aria-label="Verify token"
                      value={webhookConfig.verify_token}
                      className="flex-1 rounded border border-line bg-ground px-3 py-2 font-mono text-xs text-ink"
                    />
                    <button
                      type="button"
                      onClick={() => void handleCopy("token", webhookConfig.verify_token)}
                      className="rounded border border-line px-3 py-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                    >
                      {copied === "token" ? "Copiado!" : "Copiar"}
                    </button>
                  </div>
                </div>
              </li>
              <li>
                Ainda em Webhook, na lista{" "}
                <span className="font-medium">Webhook fields</span>, clique em Manage e assine o
                campo <code className="rounded bg-ground px-1">messages</code>.
              </li>
            </ol>
          </section>
        )}
```

- [ ] **Step 4: Rodar e ver passarem**

Run: `cd apps/web && pnpm test -- WhatsAppConnectionPanel`
Expected: PASS (todos, incluindo os 5 pré-existentes).

- [ ] **Step 5: Lint + suíte completa do web**

Run: `cd apps/web && pnpm lint && pnpm test`
Expected: sem erros.

- [ ] **Step 6: Atualizar CLAUDE.md**

Na seção "Integração WhatsApp Business", localizar o trecho (bullet "Onboarding do número"):

```
O `api` (`POST /api/v1/whatsapp/connect`) valida o token/`phone_number_id` na Graph API (`GET /{phone_number_id}`, obtém o `display_phone_number`) e registra o número (`POST /{phone_number_id}/register` com o PIN de 2 fatores) **antes** de persistir qualquer credencial — nada é salvo se a Meta rejeitar.
```

Substituir por:

```
O `api` (`POST /api/v1/whatsapp/connect`) valida o token/`phone_number_id` na Graph API (`GET /{phone_number_id}`, obtém o `display_phone_number`), registra o número (`POST /{phone_number_id}/register` com o PIN de 2 fatores) e inscreve o app do tenant na WABA (`POST /{waba_id}/subscribed_apps` — sem isso a Meta não entrega os webhooks de mensagem; falha silenciosa descoberta em produção) **antes** de persistir qualquer credencial — nada é salvo se a Meta rejeitar.
```

E, no mesmo bullet, após a frase "GET /api/v1/whatsapp/connection` e `POST /api/v1/whatsapp/disconnect` completam o ciclo (número mascarado na resposta; `access_token` nunca aparece em nenhuma resposta da API).", adicionar:

```
 A página de setup também exibe as instruções do passo manual de webhook (URL de callback + verify token prontos pra copiar, e a instrução de assinar o campo `messages`), alimentadas por `GET /api/v1/whatsapp/webhook-config` (autenticado; monta a URL a partir da env `API_PUBLIC_URL`). ⚠️ Pendência de segurança: a validação de assinatura `X-Hub-Signature-256` usa um `META_APP_SECRET` único da plataforma, mas no modelo de app-por-tenant cada app tem um App Secret próprio — por ora a validação fica efetivamente desligada em produção (env vazia); o certo é coletar e cifrar o App Secret por tenant no connect e validar por número.
```

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/components/WhatsAppConnectionPanel.tsx apps/web/__tests__/WhatsAppConnectionPanel.test.tsx CLAUDE.md
git commit -m "feat(web): instruções do webhook da Meta na página de setup do WhatsApp"
```

---

## Nota pós-deploy (manual, fora do código)

Adicionar no `.env` do VPS: `API_PUBLIC_URL=https://api.rootlab.com.br` e recriar o `api`. Sem isso a página mostra o path relativo em vez da URL completa.
