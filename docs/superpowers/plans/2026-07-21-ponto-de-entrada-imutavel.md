# Ponto de entrada imutável Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar `is_entry_point` imutável pelo usuário — só o provisionamento automático decide quem é o ponto de entrada de cada tenant, uma vez, pra sempre; nenhuma rota de API aceita criar ou promover um agente como ponto de entrada.

**Architecture:** Remove o campo `is_entry_point` dos schemas de entrada (`AgentCreate`/`AgentUpdate`) e toda a lógica de mutação associada (`_unset_current_entry_point`, os dois blocos de checagem em `update_agent`) — o campo continua existindo no model/`AgentOut` (leitura, badge) e a checagem de exclusão (`409` se `is_entry_point=True`) continua intocada, agora à prova de burla porque não existe mais nenhum jeito de mudar a flag via API. No frontend, remove os dois checkboxes (criação e edição).

**Tech Stack:** FastAPI + Pydantic + SQLAlchemy async (`apps/api`), Next.js/React (`apps/web`). Testes: `pytest` (`apps/api`), Vitest (`apps/web`).

## Global Constraints

- `AgentCreate`/`AgentUpdate` não têm mais o campo `is_entry_point` — se vier no payload, é ignorado (comportamento padrão do Pydantic, sem `extra="forbid"` — nenhum schema deste projeto usa esse padrão).
- Toda criação de agente via API sempre nasce com `is_entry_point=False`, explicitamente (não confiar no `server_default` do banco, que não reflete no objeto Python antes de um refresh real).
- A checagem de exclusão do ponto de entrada (`409` se `agent.is_entry_point == True`) não muda.
- Nenhuma migração de dado é necessária — o índice único parcial já garante exatamente 1 por tenant.

---

### Task 1: Backend — travar `is_entry_point` como imutável

**Files:**
- Modify: `apps/api/app/schemas/agents.py`
- Modify: `apps/api/app/api/v1/agents.py`
- Test: `apps/api/tests/unit/test_agents_routes.py`

**Interfaces:**
- Consumes: nada de fora deste arquivo.
- Produces: nenhuma mudança de contrato pra fora — `AgentOut` continua igual (ainda expõe `is_entry_point` pra leitura).

- [ ] **Step 1: Atualizar os testes de `TestCreate`**

Em `apps/api/tests/unit/test_agents_routes.py`, na classe `TestCreate`:

Substitua `test_cria_agente` (remove `is_entry_point` do payload, já que o campo não existe mais no schema):

```python
    def test_cria_agente(self, client, session) -> None:
        session.execute.return_value = _active_subscription()

        response = client.post(
            "/api/v1/agents",
            json={"name": "Vendas", "instructions": "Você vende planos."},
        )

        assert response.status_code == 201
        assert response.json()["name"] == "Vendas"
        assert response.json()["is_entry_point"] is False
        session.add.assert_called_once()
        added = session.add.call_args.args[0]
        assert added.tenant_id == TENANT_ID
        session.commit.assert_awaited()
```

Substitua `test_criar_como_ponto_de_entrada_desmarca_o_anterior` (esse comportamento não existe mais) por:

```python
    def test_criar_agente_com_is_entry_point_no_payload_e_ignorado(self, client, session) -> None:
        """is_entry_point não existe mais em AgentCreate — mesmo se vier no
        payload, é ignorado (Pydantic descarta campo não declarado). Todo
        agente criado via API nasce sempre com is_entry_point=False, e
        nenhuma UPDATE de desmarcar o ponto de entrada anterior roda."""
        session.execute.return_value = _active_subscription()

        response = client.post(
            "/api/v1/agents",
            json={"name": "Novo", "instructions": "x", "is_entry_point": True},
        )

        assert response.status_code == 201
        assert response.json()["is_entry_point"] is False
        added = session.add.call_args.args[0]
        assert added.is_entry_point is False
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        assert not any("UPDATE agents" in s for s in statements)
```

Os testes `test_limite_de_agentes_do_plano_retorna_409` e `test_assinatura_inativa_retorna_409` não mudam (não usam `is_entry_point` de forma relevante).

- [ ] **Step 2: Atualizar os testes de `TestUpdate`**

Remova `test_marcar_como_ponto_de_entrada_desmarca_o_anterior` e `test_desmarcar_o_unico_ponto_de_entrada_retorna_409` (comportamento não existe mais). Substitua `test_desmarcar_is_entry_point_que_ja_era_false_nao_quebra` pelos dois testes abaixo:

```python
    def test_patch_ignora_is_entry_point_true_no_payload(self, client, session) -> None:
        """is_entry_point não existe mais em AgentUpdate — enviar True não
        promove o agente a ponto de entrada."""
        session.scalar.return_value = _agent(is_entry_point=False)

        response = client.patch(
            f"/api/v1/agents/{AGENT_ID}", json={"is_entry_point": True}
        )

        assert response.status_code == 200
        assert response.json()["is_entry_point"] is False
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        assert not any("UPDATE agents" in s for s in statements)

    def test_patch_ignora_is_entry_point_false_no_payload(self, client, session) -> None:
        """Enviar False também não desmarca — o ponto de entrada atual do
        tenant nunca muda via PATCH, em nenhuma direção."""
        session.scalar.return_value = _agent(is_entry_point=True)

        response = client.patch(
            f"/api/v1/agents/{AGENT_ID}", json={"name": "Nome novo", "is_entry_point": False}
        )

        assert response.status_code == 200
        assert response.json()["is_entry_point"] is True
        assert response.json()["name"] == "Nome novo"
        statements = [str(call.args[0]) for call in session.execute.await_args_list]
        assert not any("UPDATE agents" in s for s in statements)
```

`test_edita_nome_e_instrucoes` e `test_agente_de_outro_tenant_retorna_404` não mudam.

- [ ] **Step 3: Rodar os testes e confirmar a falha**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_routes.py -v -k "TestCreate or TestUpdate"`
Expected: FAIL — os 3 testes novos falham (o campo `is_entry_point` ainda existe nos schemas e a lógica de mutação ainda roda), e os 2 testes removidos/substituídos não existem mais nesse ponto (só existirão depois do Step 1/2, o que já foi feito — a falha esperada aqui é dos comportamentos ainda não implementados no Step 4).

- [ ] **Step 4: Remover `is_entry_point` dos schemas**

Em `apps/api/app/schemas/agents.py`, substitua:

```python
class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1)
    is_entry_point: bool = False


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    instructions: str | None = Field(default=None, min_length=1)
    is_entry_point: bool | None = None
```

por:

```python
class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1)


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    instructions: str | None = Field(default=None, min_length=1)
```

- [ ] **Step 5: Remover a lógica de mutação em `agents.py`**

Em `apps/api/app/api/v1/agents.py`, o import no topo do arquivo:

```python
from sqlalchemy import func, select, update
```

vira:

```python
from sqlalchemy import func, select
```

Remova a função `_unset_current_entry_point` inteira:

```python
async def _unset_current_entry_point(ctx: TenantContext, session: AsyncSession) -> None:
    await session.execute(
        update(Agent)
        .where(Agent.tenant_id == ctx.tenant_id, Agent.is_entry_point.is_(True))
        .values(is_entry_point=False)
    )
```

Em `create_agent`, substitua:

```python
    if body.is_entry_point:
        await _unset_current_entry_point(ctx, session)

    agent = Agent(id=uuid.uuid4(), tenant_id=ctx.tenant_id, **body.model_dump())
    session.add(agent)
```

por:

```python
    agent = Agent(
        id=uuid.uuid4(), tenant_id=ctx.tenant_id, is_entry_point=False, **body.model_dump()
    )
    session.add(agent)
```

(`is_entry_point=False` é passado explicitamente — o model só tem `server_default`, não um default Python, então sem isso o atributo fica `None` no objeto antes de um refresh real contra o banco.)

Em `update_agent`, substitua:

```python
    agent = await _get_agent(agent_id, ctx, session)

    if body.is_entry_point is False and agent.is_entry_point:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Não é possível desmarcar o único ponto de entrada — "
                "marque outro agente como ponto de entrada antes"
            ),
        )

    if body.is_entry_point is True and not agent.is_entry_point:
        await _unset_current_entry_point(ctx, session)

    for field, value in body.model_dump(exclude_unset=True).items():
```

por:

```python
    agent = await _get_agent(agent_id, ctx, session)

    for field, value in body.model_dump(exclude_unset=True).items():
```

`delete_agent` não muda.

- [ ] **Step 6: Rodar os testes e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_routes.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 7: Rodar a suíte completa + lint**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v && uv run ruff check .`
Expected: todos passam (ou skip nos de integração que exigem Postgres real), lint limpo.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/schemas/agents.py apps/api/app/api/v1/agents.py apps/api/tests/unit/test_agents_routes.py
git commit -m "feat(api): trava is_entry_point como imutável — só o provisionamento decide"
```

---

### Task 2: Frontend — remover os checkboxes de ponto de entrada

**Files:**
- Modify: `apps/web/src/components/AgentsPanel.tsx`
- Modify: `apps/web/src/components/AgentDetail.tsx`
- Test: `apps/web/__tests__/AgentsPanel.test.tsx` (sem mudança de conteúdo — só confirmar que continua passando)
- Test: `apps/web/__tests__/AgentDetail.test.tsx` (sem mudança de conteúdo — só confirmar que continua passando)

**Interfaces:**
- Consumes: nenhuma (Task 1 já mergeada — o backend agora ignora `is_entry_point` em `POST`/`PATCH`, então mesmo sem esta task o campo já seria inofensivo; esta task só limpa a UI).
- Produces: nada consumido por outra task deste plano (última task).

- [ ] **Step 1: Confirmar a baseline dos testes de frontend antes da mudança**

Run: `cd apps/web && pnpm vitest run __tests__/AgentsPanel.test.tsx __tests__/AgentDetail.test.tsx`
Expected: todos passam (nenhum teste hoje interage com o checkbox ou verifica `is_entry_point` no corpo do POST/PATCH — confirmado lendo os dois arquivos de teste).

- [ ] **Step 2: Remover o checkbox de criação em `AgentsPanel.tsx`**

Em `apps/web/src/components/AgentsPanel.tsx`, troque:

```typescript
const EMPTY_FORM = { name: "", instructions: "", is_entry_point: false };
```

por:

```typescript
const EMPTY_FORM = { name: "", instructions: "" };
```

Remova o bloco do checkbox (entre o `<textarea>` de Instruções e o `<button type="submit">`):

```tsx
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={form.is_entry_point}
              onChange={(event) => setForm({ ...form, is_entry_point: event.target.checked })}
            />
            Ponto de entrada (recebe a primeira mensagem de conversas novas)
          </label>
```

- [ ] **Step 3: Remover o toggle de edição em `AgentDetail.tsx`**

Em `apps/web/src/components/AgentDetail.tsx`:

Remova a linha de estado:

```typescript
  const [isEntryPoint, setIsEntryPoint] = useState(false);
```

No `load()`, remova a linha:

```typescript
          setIsEntryPoint(found.is_entry_point);
```

(dentro do bloco `if (found) { setName(found.name); setInstructions(found.instructions); ... }` — mantenha as outras duas linhas.)

Em `handleSave`, troque:

```typescript
        body: JSON.stringify({ name, instructions, is_entry_point: isEntryPoint }),
```

por:

```typescript
        body: JSON.stringify({ name, instructions }),
```

E remova o bloco de reversão do toggle em caso de erro:

```typescript
        // Reverte o toggle de ponto de entrada pro último valor confirmado —
        // sem isso a caixa fica marcada mesmo com o PATCH tendo falhado.
        if (agent) setIsEntryPoint(agent.is_entry_point);
```

(deixando só `setFeedback(...)` e `return;` no bloco `if (!response.ok) { ... }`.)

Depois do `setAgent(body);`, remova:

```typescript
      setIsEntryPoint(body.is_entry_point);
```

Remova o bloco do checkbox no JSX (entre o `<textarea>` de Instruções e o `<button type="submit">`):

```tsx
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={isEntryPoint}
              onChange={(event) => setIsEntryPoint(event.target.checked)}
            />
            Ponto de entrada (recebe a primeira mensagem de conversas novas)
          </label>
```

- [ ] **Step 4: Rodar os testes de frontend e confirmar sucesso**

Run: `cd apps/web && pnpm vitest run __tests__/AgentsPanel.test.tsx __tests__/AgentDetail.test.tsx`
Expected: todos passam, sem nenhuma mudança de asserção necessária.

- [ ] **Step 5: Rodar lint + build**

Run: `cd apps/web && pnpm lint && pnpm build`
Expected: sem erros novos (build confirma que não sobrou nenhuma referência a `isEntryPoint`/`setIsEntryPoint`/`form.is_entry_point` órfã — TypeScript pegaria isso).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/AgentsPanel.tsx apps/web/src/components/AgentDetail.tsx
git commit -m "feat(web): remove os checkboxes de ponto de entrada — campo agora é somente leitura"
```
