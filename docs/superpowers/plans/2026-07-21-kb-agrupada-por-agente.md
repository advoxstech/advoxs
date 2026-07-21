# Base de conhecimento agrupada por agente Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidar a gestão de base de conhecimento por agente inteiramente em `/base-de-conhecimento`, apresentada como uma árvore expansível (1 pasta por agente), preservando o N:N já existente (um arquivo pode aparecer em mais de uma pasta); simplificar `/agentes/[id]` pra um resumo somente-leitura.

**Architecture:** Backend ganha o dado que faltava (`agent_ids` por arquivo em `GET /knowledge-base/files`) e um guard novo (não deixar um arquivo sem nenhum agente). Frontend ganha um componente novo (`AgentFolder.tsx`, 1 pasta) consumido por uma reescrita de `KnowledgeBasePanel.tsx` (orquestra fetch + monta a árvore), e `AgentDetail.tsx` perde toda a UI de attach/detach.

**Tech Stack:** FastAPI + Pydantic + SQLAlchemy async (`apps/api`), Next.js/React (`apps/web`). Testes: `pytest` (`apps/api`), Vitest (`apps/web`).

## Global Constraints

- `agent_ids` (lista de UUIDs) é o dado novo que trafega em `KnowledgeBaseFileOut` — nunca remove nenhum campo já existente do schema.
- N:N preservado — nenhuma mudança de schema/model, só de comportamento da API (o que `GET` devolve, o que `DELETE` recusa).
- Desanexar o último vínculo de um arquivo (deixando ele sem nenhum agente) é recusado com `409` — mesmo padrão já usado pra não deixar o tenant sem agente/sem ponto de entrada.
- `/base-de-conhecimento` deixa de ter um `<select>` de "agente de destino" no topo — cada pasta tem seu próprio botão de upload, já escopado.
- `/agentes/[id]` perde toda gestão de KB (attach/detach/upload direto), mantendo só uma contagem + link.

---

### Task 1: Backend — `agent_ids` em `GET /knowledge-base/files`

**Files:**
- Modify: `apps/api/app/schemas/knowledge_base.py`
- Modify: `apps/api/app/api/v1/knowledge_base.py`
- Test: `apps/api/tests/unit/test_knowledge_base_routes.py`
- Test: `apps/api/tests/unit/test_agents_routes.py`

**Interfaces:**
- Consumes: nada de fora deste arquivo.
- Produces: `KnowledgeBaseFileOut.agent_ids: list[uuid.UUID]` — consumido pela Task 3 (frontend, monta a árvore a partir desse campo).

- [ ] **Step 1: Escrever os testes que falham**

Em `apps/api/tests/unit/test_knowledge_base_routes.py`, atualize `test_upload_feliz_enfileira_apos_commit` (dentro de `class TestUpload`) adicionando 1 linha de asserção, logo depois de `assert body["filename"] == "regimento.pdf"`:

```python
        assert body["agent_ids"] == [str(AGENT_ID)]
```

Substitua `test_lista_arquivos_do_tenant` (dentro de `class TestList`) por:

```python
class TestList:
    def test_lista_arquivos_do_tenant(self, client, session) -> None:
        files_result = MagicMock()
        files_result.scalars.return_value.all.return_value = [_record()]
        links_result = MagicMock()
        links_result.all.return_value = [(FILE_ID, AGENT_ID)]
        session.execute.side_effect = [files_result, links_result]

        response = client.get("/api/v1/knowledge-base/files")

        assert response.status_code == 200
        body = response.json()
        assert body[0]["filename"] == "regimento.pdf"
        assert body[0]["agent_ids"] == [str(AGENT_ID)]

    def test_lista_arquivos_com_multiplos_agentes_e_sem_agente(self, client, session) -> None:
        """Cobre as 3 cardinalidades que a árvore do frontend precisa
        distinguir: arquivo em 2+ agentes (aparece nas 2 pastas) e arquivo
        em 0 agentes (não aparece em nenhuma pasta, mas continua na lista)."""
        outro_file_id = uuid.uuid4()
        outro_agent_id = uuid.uuid4()
        arquivo_sem_agente = SimpleNamespace(
            id=outro_file_id,
            tenant_id=TENANT_ID,
            filename="orfao.pdf",
            size_bytes=500,
            mime_type="application/pdf",
            status="ready",
            error_message=None,
            uploaded_at=datetime.now(UTC),
        )
        files_result = MagicMock()
        files_result.scalars.return_value.all.return_value = [_record(), arquivo_sem_agente]
        links_result = MagicMock()
        links_result.all.return_value = [(FILE_ID, AGENT_ID), (FILE_ID, outro_agent_id)]
        session.execute.side_effect = [files_result, links_result]

        response = client.get("/api/v1/knowledge-base/files")

        assert response.status_code == 200
        regimento, orfao = response.json()
        assert sorted(regimento["agent_ids"]) == sorted([str(AGENT_ID), str(outro_agent_id)])
        assert orfao["agent_ids"] == []
```

- [ ] **Step 2: Rodar os testes e confirmar a falha**

Run: `cd apps/api && uv run pytest tests/unit/test_knowledge_base_routes.py -v -k "TestUpload or TestList"`
Expected: FAIL — `KeyError: 'agent_ids'` (o campo ainda não existe no schema) e/ou `AssertionError` nos testes de `TestList` (2ª chamada de `session.execute` ainda não é esperada pelo código atual).

- [ ] **Step 3: Adicionar `agent_ids` ao schema**

Em `apps/api/app/schemas/knowledge_base.py`, troque:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class KnowledgeBaseFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    size_bytes: int
    mime_type: str
    status: str
    error_message: str | None = None
    uploaded_at: datetime
```

por:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeBaseFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    size_bytes: int
    mime_type: str
    status: str
    error_message: str | None = None
    uploaded_at: datetime
    agent_ids: list[uuid.UUID] = Field(default_factory=list)
```

- [ ] **Step 4: Popular `agent_ids` em `upload_file` e `list_files`**

Em `apps/api/app/api/v1/knowledge_base.py`, na função `upload_file`, troque a linha final:

```python
    return KnowledgeBaseFileOut.model_validate(record)
```

por:

```python
    return KnowledgeBaseFileOut(
        id=record.id,
        filename=record.filename,
        size_bytes=record.size_bytes,
        mime_type=record.mime_type,
        status=record.status,
        error_message=record.error_message,
        uploaded_at=record.uploaded_at,
        agent_ids=[agent_id],
    )
```

Troque a função `list_files` inteira:

```python
@router.get("/files")
async def list_files(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[KnowledgeBaseFileOut]:
    result = await session.execute(
        select(KnowledgeBaseFile)
        .where(KnowledgeBaseFile.tenant_id == ctx.tenant_id)
        .order_by(KnowledgeBaseFile.uploaded_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [KnowledgeBaseFileOut.model_validate(f) for f in result.scalars().all()]
```

por:

```python
@router.get("/files")
async def list_files(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[KnowledgeBaseFileOut]:
    result = await session.execute(
        select(KnowledgeBaseFile)
        .where(KnowledgeBaseFile.tenant_id == ctx.tenant_id)
        .order_by(KnowledgeBaseFile.uploaded_at.desc())
        .limit(limit)
        .offset(offset)
    )
    files = result.scalars().all()

    links_result = await session.execute(
        select(
            AgentKnowledgeBaseFile.knowledge_base_file_id, AgentKnowledgeBaseFile.agent_id
        ).where(AgentKnowledgeBaseFile.knowledge_base_file_id.in_([f.id for f in files]))
    )
    agent_ids_by_file: dict[uuid.UUID, list[uuid.UUID]] = {}
    for file_id, agent_id in links_result.all():
        agent_ids_by_file.setdefault(file_id, []).append(agent_id)

    return [
        KnowledgeBaseFileOut(
            id=f.id,
            filename=f.filename,
            size_bytes=f.size_bytes,
            mime_type=f.mime_type,
            status=f.status,
            error_message=f.error_message,
            uploaded_at=f.uploaded_at,
            agent_ids=agent_ids_by_file.get(f.id, []),
        )
        for f in files
    ]
```

- [ ] **Step 5: Rodar os testes de knowledge_base e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_knowledge_base_routes.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 6: Confirmar que `list_agent_knowledge_base_files` (em `agents.py`) não quebrou**

Esse endpoint (`GET /agents/{id}/knowledge-base-files`) também devolve `list[KnowledgeBaseFileOut]`, mas via `KnowledgeBaseFileOut.model_validate(f)` num `KnowledgeBaseFile` puro (sem `agent_ids`) — o campo novo tem `default_factory=list`, então o Pydantic deveria cair no default quando o atributo não existe no objeto de origem.

Run: `cd apps/api && uv run pytest tests/unit/test_agents_routes.py -v -k TestListKnowledgeBaseFiles`
Expected: `test_lista_arquivos_anexados` e `test_agente_inexistente_retorna_404` passam sem nenhuma mudança de código nesse arquivo.

**Se `test_lista_arquivos_anexados` falhar** (comportamento do Pydantic diferente do esperado num objeto sem o atributo): abra `apps/api/tests/unit/test_agents_routes.py`, localize o fixture `file_row` dentro de `test_lista_arquivos_anexados` (`class TestListKnowledgeBaseFiles`), e adicione `agent_ids=[]` aos campos do `SimpleNamespace`:

```python
        file_row = SimpleNamespace(
            id=uuid.uuid4(),
            filename="regimento.pdf",
            size_bytes=1024,
            mime_type="application/pdf",
            status="ready",
            error_message=None,
            uploaded_at=datetime.now(UTC),
            agent_ids=[],
        )
```

E rode o teste de novo pra confirmar.

- [ ] **Step 7: Rodar a suíte completa + lint**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v && uv run ruff check .`
Expected: todos passam (ou skip nos de integração que exigem Postgres real), lint limpo.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/schemas/knowledge_base.py apps/api/app/api/v1/knowledge_base.py apps/api/tests/unit/test_knowledge_base_routes.py apps/api/tests/unit/test_agents_routes.py
git commit -m "feat(api): GET /knowledge-base/files devolve agent_ids por arquivo"
```

---

### Task 2: Backend — recusa desanexar o último agente de um arquivo

**Files:**
- Modify: `apps/api/app/api/v1/agents.py`
- Test: `apps/api/tests/unit/test_agents_routes.py`

**Interfaces:**
- Consumes: nada de fora deste arquivo (Task 1 é independente desta).
- Produces: nada consumido por outra task deste plano — o frontend (Task 3) só precisa saber que esse `409` existe pra desabilitar o botão de "desanexar" quando `file.agent_ids.length <= 1`, mas isso é uma decisão de UI, não uma dependência de tipo/contrato.

- [ ] **Step 1: Atualizar o teste existente e adicionar o novo**

Em `apps/api/tests/unit/test_agents_routes.py`, na classe `TestDetachKnowledgeBaseFile`, troque `test_desanexa_arquivo`:

```python
    def test_desanexa_arquivo(self, client, session) -> None:
        session.scalar.return_value = _agent()
        link = SimpleNamespace(agent_id=AGENT_ID, knowledge_base_file_id=uuid.uuid4())
        session.get = AsyncMock(return_value=link)

        response = client.delete(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files/{link.knowledge_base_file_id}"
        )

        assert response.status_code == 204
        session.delete.assert_awaited_once_with(link)
```

por:

```python
    def test_desanexa_arquivo(self, client, session) -> None:
        # 1ª scalar: _get_agent; 2ª: contagem de vínculos do arquivo (>1, permite).
        session.scalar.side_effect = [_agent(), 2]
        link = SimpleNamespace(agent_id=AGENT_ID, knowledge_base_file_id=uuid.uuid4())
        session.get = AsyncMock(return_value=link)

        response = client.delete(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files/{link.knowledge_base_file_id}"
        )

        assert response.status_code == 204
        session.delete.assert_awaited_once_with(link)
```

Adicione, no fim de `class TestDetachKnowledgeBaseFile`:

```python
    def test_desanexar_ultimo_vinculo_retorna_409(self, client, session) -> None:
        session.scalar.side_effect = [_agent(), 1]
        link = SimpleNamespace(agent_id=AGENT_ID, knowledge_base_file_id=uuid.uuid4())
        session.get = AsyncMock(return_value=link)

        response = client.delete(
            f"/api/v1/agents/{AGENT_ID}/knowledge-base-files/{link.knowledge_base_file_id}"
        )

        assert response.status_code == 409
        session.delete.assert_not_awaited()
```

`test_vinculo_inexistente_retorna_404` não muda (o `409` de guarda só é checado depois de confirmar que o vínculo existe).

- [ ] **Step 2: Rodar os testes e confirmar a falha**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_routes.py -v -k TestDetachKnowledgeBaseFile`
Expected: FAIL — `test_desanexa_arquivo` quebra (`session.scalar` só é chamado 1 vez hoje, o `side_effect` de 2 itens não é consumido, mas a asserção de status ainda deveria passar por acidente; o teste que realmente prova a falha é `test_desanexar_ultimo_vinculo_retorna_409`, que espera `409` mas hoje recebe `204`).

- [ ] **Step 3: Implementar o guard**

Em `apps/api/app/api/v1/agents.py`, na função `detach_knowledge_base_file`, troque:

```python
    link = await session.get(AgentKnowledgeBaseFile, (agent_id, file_id))
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vínculo não encontrado")

    await session.delete(link)
    await session.commit()
```

por:

```python
    link = await session.get(AgentKnowledgeBaseFile, (agent_id, file_id))
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vínculo não encontrado")

    total_links = await session.scalar(
        select(func.count())
        .select_from(AgentKnowledgeBaseFile)
        .where(AgentKnowledgeBaseFile.knowledge_base_file_id == file_id)
    )
    if total_links <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Não é possível desanexar o último agente deste arquivo — "
                "exclua o arquivo se não for mais usar"
            ),
        )

    await session.delete(link)
    await session.commit()
```

(`func` e `select` já estão importados no topo do arquivo.)

- [ ] **Step 4: Rodar os testes e confirmar sucesso**

Run: `cd apps/api && uv run pytest tests/unit/test_agents_routes.py -v`
Expected: todos os testes do arquivo passam.

- [ ] **Step 5: Rodar a suíte completa + lint**

Run: `cd apps/api && uv run pytest tests/unit tests/integration -v && uv run ruff check .`
Expected: todos passam (ou skip), lint limpo.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/api/v1/agents.py apps/api/tests/unit/test_agents_routes.py
git commit -m "feat(api): recusa desanexar o último agente de um arquivo de KB"
```

---

### Task 3: Frontend — árvore em `/base-de-conhecimento`

**Files:**
- Create: `apps/web/src/components/AgentFolder.tsx`
- Modify: `apps/web/src/components/KnowledgeBasePanel.tsx`
- Test: `apps/web/__tests__/KnowledgeBasePanel.test.tsx`

**Interfaces:**
- Consumes: `agent_ids: string[]` em cada item de `GET /knowledge-base/files` (Task 1); `409` de `DELETE /agents/{id}/knowledge-base-files/{file_id}` quando é o último vínculo (Task 2, só relevante pro texto de erro exibido, sem contrato de tipo).
- Produces: `AgentFolder` (componente, props documentadas no Step 3) — não é consumido por nenhuma outra task deste plano.

- [ ] **Step 1: Escrever o teste que falha**

Substitua o conteúdo inteiro de `apps/web/__tests__/KnowledgeBasePanel.test.tsx` por:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { KnowledgeBasePanel } from "@/components/KnowledgeBasePanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const agents = [
  {
    id: "a1",
    name: "Secretária",
    instructions: "x",
    is_entry_point: true,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
  },
  {
    id: "a2",
    name: "Condominial",
    instructions: "y",
    is_entry_point: false,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
  },
];

const files = [
  {
    id: "f1",
    filename: "regimento.pdf",
    size_bytes: 1048576,
    mime_type: "application/pdf",
    status: "ready",
    error_message: null,
    uploaded_at: "2026-07-08T12:00:00Z",
    agent_ids: ["a1"],
  },
  {
    id: "f2",
    filename: "contrato.docx",
    size_bytes: 2048,
    mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    status: "error",
    error_message: "Falha na ingestão (HTTP 400)",
    uploaded_at: "2026-07-08T11:00:00Z",
    agent_ids: ["a1", "a2"],
  },
];

function mockRouting(postHandler?: (path: string, init: RequestInit) => unknown) {
  mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "agents") return { ok: true, status: 200, json: async () => agents };
    if (path === "knowledge-base/files" && (!init || !init.method)) {
      return { ok: true, status: 200, json: async () => files };
    }
    if (init?.method === "POST" || init?.method === "DELETE") {
      return postHandler
        ? postHandler(path, init)
        : { ok: true, status: 200, json: async () => null };
    }
    return { ok: true, status: 200, json: async () => null };
  });
}

describe("KnowledgeBasePanel", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
    window.history.pushState({}, "", "/base-de-conhecimento");
  });

  it("renderiza 1 pasta por agente, incluindo agente sem arquivos", async () => {
    mockRouting();

    render(<KnowledgeBasePanel pollMs={0} />);

    await waitFor(() => expect(screen.getByText("Secretária")).toBeInTheDocument());
    expect(screen.getByText("Condominial")).toBeInTheDocument();
    expect(screen.getByText("[2 arquivos]")).toBeInTheDocument();
    expect(screen.getByText("[1 arquivo]")).toBeInTheDocument();
  });

  it("um arquivo em 2 agentes aparece nas 2 pastas", async () => {
    mockRouting();

    render(<KnowledgeBasePanel pollMs={0} />);

    // Secretária (ponto de entrada) começa expandida por padrão.
    await waitFor(() => expect(screen.getByText("contrato.docx")).toBeInTheDocument());
    expect(screen.getByText("regimento.pdf")).toBeInTheDocument();

    // Condominial começa recolhida — expande pra confirmar que o mesmo
    // arquivo também aparece lá.
    fireEvent.click(screen.getByText("Condominial"));
    await waitFor(() => expect(screen.getAllByText("contrato.docx")).toHaveLength(2));
    expect(screen.queryAllByText("regimento.pdf")).toHaveLength(1);
  });

  it("pré-expande a pasta vinda da URL (?agent_id=)", async () => {
    window.history.pushState({}, "", "/base-de-conhecimento?agent_id=a2");
    mockRouting();

    render(<KnowledgeBasePanel pollMs={0} />);

    await waitFor(() => expect(screen.getByText("contrato.docx")).toBeInTheDocument());
    expect(screen.queryByText("regimento.pdf")).not.toBeInTheDocument();
  });

  it("envia o agent_id certo no upload de cada pasta", async () => {
    let capturedForm: FormData | null = null;
    mockRouting((_path, init) => {
      capturedForm = init.body as FormData;
      return { ok: true, status: 202, json: async () => files[0] };
    });

    render(<KnowledgeBasePanel pollMs={0} />);
    await waitFor(() => expect(screen.getByText("Condominial")).toBeInTheDocument());

    const file = new File(["conteudo"], "novo.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Enviar arquivo para Condominial"), {
      target: { files: [file] },
    });

    await waitFor(() => expect(capturedForm).not.toBeNull());
    expect(capturedForm!.get("agent_id")).toBe("a2");
  });

  it("anexa um arquivo já existente a outro agente pelo seletor inline", async () => {
    let capturedPath = "";
    let capturedBody = "";
    mockRouting((path, init) => {
      capturedPath = path;
      capturedBody = init.body as string;
      return { ok: true, status: 201, json: async () => ({ knowledge_base_file_id: "f1" }) };
    });

    render(<KnowledgeBasePanel pollMs={0} />);
    await waitFor(() => expect(screen.getByText("regimento.pdf")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Anexar regimento.pdf a outro agente"), {
      target: { value: "a2" },
    });

    await waitFor(() => expect(capturedPath).toBe("agents/a2/knowledge-base-files"));
    expect(JSON.parse(capturedBody).knowledge_base_file_id).toBe("f1");
  });

  it("desabilita 'desanexar' quando o arquivo só tem 1 agente", async () => {
    mockRouting();

    render(<KnowledgeBasePanel pollMs={0} />);
    await waitFor(() => expect(screen.getByText("regimento.pdf")).toBeInTheDocument());

    expect(screen.getByLabelText("Desanexar regimento.pdf deste agente")).toBeDisabled();
    expect(screen.getByLabelText("Desanexar contrato.docx deste agente")).not.toBeDisabled();
  });

  it("exclui um arquivo após confirmação", async () => {
    // handleDelete chama load() de novo após o DELETE — o mock precisa
    // simular o backend removendo o arquivo, senão o GET seguinte devolve
    // a mesma lista estática e o teste nunca vê o arquivo desaparecer.
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    let deleted = false;
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "agents") return { ok: true, status: 200, json: async () => agents };
      if (path === "knowledge-base/files/f1" && init?.method === "DELETE") {
        deleted = true;
        return { ok: true, status: 204, json: async () => null };
      }
      if (path === "knowledge-base/files") {
        return { ok: true, status: 200, json: async () => (deleted ? files.slice(1) : files) };
      }
      return { ok: true, status: 200, json: async () => null };
    });

    render(<KnowledgeBasePanel pollMs={0} />);
    await waitFor(() => expect(screen.getByText("regimento.pdf")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Excluir regimento.pdf"));

    await waitFor(() => expect(screen.queryByText("regimento.pdf")).not.toBeInTheDocument());
    confirmSpy.mockRestore();
  });
});
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd apps/web && pnpm vitest run __tests__/KnowledgeBasePanel.test.tsx`
Expected: FAIL — `AgentFolder` não existe, `KnowledgeBasePanel` ainda usa o `<select>`/lista linear antigos.

- [ ] **Step 3: Criar `AgentFolder.tsx`**

Crie `apps/web/src/components/AgentFolder.tsx`:

```tsx
"use client";

import { useRef, useState } from "react";

import type { Agent } from "@/lib/types";

export type KbFile = {
  id: string;
  filename: string;
  size_bytes: number;
  mime_type: string;
  status: "processing" | "ready" | "error";
  error_message: string | null;
  uploaded_at: string;
  agent_ids: string[];
};

const ACCEPTED = ".pdf,.docx,.txt";

const STATUS_LABEL: Record<KbFile["status"], string> = {
  processing: "processando",
  ready: "pronto",
  error: "erro",
};

const STATUS_CLASS: Record<KbFile["status"], string> = {
  processing: "bg-brass-soft text-brass",
  ready: "bg-accent-soft text-accent",
  error: "bg-danger/10 text-danger",
};

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

export function AgentFolder({
  agent,
  files,
  allAgents,
  defaultExpanded,
  uploading,
  onUpload,
  onAttach,
  onDetach,
  onDelete,
}: {
  agent: Agent;
  files: KbFile[];
  allAgents: Agent[];
  defaultExpanded: boolean;
  uploading: boolean;
  onUpload: (agentId: string, file: File) => void;
  onAttach: (fileId: string, agentId: string) => void;
  onDetach: (agentId: string, fileId: string) => void;
  onDelete: (file: KbFile) => void;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <section className="border-b border-line py-3">
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="flex flex-1 items-center gap-2 text-left"
        >
          <span className="text-muted">{expanded ? "▾" : "▸"}</span>
          <span className="font-medium text-ink">{agent.name}</span>
          {agent.is_entry_point && (
            <span className="rounded-full bg-accent-soft px-2 py-0.5 font-mono text-[9px] uppercase tracking-[0.15em] text-accent">
              ponto de entrada
            </span>
          )}
          <span className="text-xs text-muted">
            [{files.length} arquivo{files.length === 1 ? "" : "s"}]
          </span>
        </button>
        <label
          className={`cursor-pointer whitespace-nowrap rounded border border-line bg-surface px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent ${uploading ? "pointer-events-none opacity-50" : ""}`}
        >
          + Enviar arquivo
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED}
            aria-label={`Enviar arquivo para ${agent.name}`}
            className="hidden"
            onChange={(event) => {
              const selected = event.target.files?.[0];
              if (selected) onUpload(agent.id, selected);
              if (inputRef.current) inputRef.current.value = "";
            }}
          />
        </label>
      </div>

      {expanded && (
        <ul className="ml-6 mt-2">
          {files.length === 0 && (
            <li className="py-2 text-sm text-muted">Nenhum arquivo anexado ainda.</li>
          )}
          {files.map((file) => {
            const availableToAttach = allAgents.filter(
              (a) => a.id !== agent.id && !file.agent_ids.includes(a.id),
            );
            return (
              <li
                key={file.id}
                className="flex items-center gap-3 border-b border-line py-3 last:border-b-0"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-ink">{file.filename}</p>
                  <p className="text-xs text-muted">
                    {formatSize(file.size_bytes)} ·{" "}
                    {new Date(file.uploaded_at).toLocaleDateString("pt-BR")}
                  </p>
                  {file.status === "error" && file.error_message && (
                    <p className="mt-1 text-xs text-danger">{file.error_message}</p>
                  )}
                </div>
                <span
                  className={`rounded-full px-3 py-1 font-mono text-[10px] uppercase tracking-[0.15em] ${STATUS_CLASS[file.status]}`}
                >
                  {STATUS_LABEL[file.status]}
                </span>
                {availableToAttach.length > 0 && (
                  <select
                    value=""
                    aria-label={`Anexar ${file.filename} a outro agente`}
                    onChange={(event) => {
                      if (event.target.value) onAttach(file.id, event.target.value);
                    }}
                    className="rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
                  >
                    <option value="">+ anexar a outro agente</option>
                    {availableToAttach.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                )}
                <button
                  type="button"
                  onClick={() => onDetach(agent.id, file.id)}
                  disabled={file.agent_ids.length <= 1}
                  aria-label={`Desanexar ${file.filename} deste agente`}
                  title={
                    file.agent_ids.length <= 1
                      ? "Este é o único agente anexado — exclua o arquivo se não for mais usar"
                      : undefined
                  }
                  className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger disabled:opacity-40"
                >
                  Desanexar
                </button>
                <button
                  type="button"
                  onClick={() => onDelete(file)}
                  disabled={file.status === "processing"}
                  aria-label={`Excluir ${file.filename}`}
                  className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger disabled:opacity-40"
                >
                  Excluir
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Reescrever `KnowledgeBasePanel.tsx`**

Substitua o conteúdo inteiro de `apps/web/src/components/KnowledgeBasePanel.tsx` por:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";

import { AgentFolder } from "@/components/AgentFolder";
import type { KbFile } from "@/components/AgentFolder";
import { backendFetch } from "@/lib/client-api";
import type { Agent } from "@/lib/types";

const MAX_FILE_BYTES = 20 * 1024 * 1024;

export function KnowledgeBasePanel({ pollMs = 5000 }: { pollMs?: number }) {
  const [files, setFiles] = useState<KbFile[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [focusedAgentId] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("agent_id"),
  );
  const [feedback, setFeedback] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await backendFetch("knowledge-base/files");
      if (response.ok) {
        setFiles(await response.json());
      }
    } catch {
      // rede indisponível: mantém a lista atual e tenta no próximo ciclo
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    async function loadAgents() {
      try {
        const response = await backendFetch("agents");
        if (response.ok) setAgents(await response.json());
      } catch {
        // fail-safe: sem agentes carregados, nenhuma pasta é exibida
      }
    }
    void loadAgents();
  }, []);

  const hasProcessing = files.some((file) => file.status === "processing");

  useEffect(() => {
    if (!pollMs || !hasProcessing) return;
    const interval = setInterval(() => void load(), pollMs);
    return () => clearInterval(interval);
  }, [load, pollMs, hasProcessing]);

  async function handleUpload(agentId: string, selected: File) {
    setFeedback(null);
    const extension = selected.name.slice(selected.name.lastIndexOf(".")).toLowerCase();
    if (![".pdf", ".docx", ".txt"].includes(extension)) {
      setFeedback("Formato não suportado — envie PDF, DOCX ou TXT.");
      return;
    }
    if (selected.size > MAX_FILE_BYTES) {
      setFeedback("Arquivo excede o limite de 20 MB.");
      return;
    }
    const form = new FormData();
    form.append("file", selected);
    form.append("agent_id", agentId);
    setUploading(true);
    try {
      const response = await backendFetch("knowledge-base/files", { method: "POST", body: form });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(body?.detail ?? "Falha no upload — tente novamente.");
        return;
      }
      await load();
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setUploading(false);
    }
  }

  async function handleAttach(fileId: string, agentId: string) {
    setFeedback(null);
    try {
      const response = await backendFetch(`agents/${agentId}/knowledge-base-files`, {
        method: "POST",
        body: JSON.stringify({ knowledge_base_file_id: fileId }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(body?.detail ?? "Falha ao anexar — tente novamente.");
        return;
      }
      await load();
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    }
  }

  async function handleDetach(agentId: string, fileId: string) {
    setFeedback(null);
    try {
      const response = await backendFetch(`agents/${agentId}/knowledge-base-files/${fileId}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(body?.detail ?? "Falha ao desanexar — tente novamente.");
        return;
      }
      await load();
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    }
  }

  async function handleDelete(file: KbFile) {
    if (!window.confirm(`Excluir "${file.filename}" da base de conhecimento?`)) return;
    try {
      const response = await backendFetch(`knowledge-base/files/${file.id}`, { method: "DELETE" });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(body?.detail ?? "Falha ao excluir — tente novamente.");
        return;
      }
      await load();
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    }
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      <header className="border-b border-line px-8 py-5">
        <h1 className="font-display text-xl font-semibold text-ink">Base de conhecimento</h1>
        <p className="text-sm text-muted">
          PDF, DOCX ou TXT, até 20 MB — organizada por agente. Um arquivo pode ser anexado a mais
          de um.
        </p>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-4">
        {agents.length === 0 && (
          <p className="py-10 text-center text-sm text-muted">Nenhum agente cadastrado ainda.</p>
        )}
        {agents.map((agent) => (
          <AgentFolder
            key={agent.id}
            agent={agent}
            files={files.filter((f) => f.agent_ids.includes(agent.id))}
            allAgents={agents}
            defaultExpanded={focusedAgentId ? agent.id === focusedAgentId : agent.is_entry_point}
            uploading={uploading}
            onUpload={handleUpload}
            onAttach={handleAttach}
            onDetach={handleDetach}
            onDelete={handleDelete}
          />
        ))}
      </div>
    </main>
  );
}
```

- [ ] **Step 5: Rodar e confirmar sucesso**

Run: `cd apps/web && pnpm vitest run __tests__/KnowledgeBasePanel.test.tsx`
Expected: todos os testes do arquivo passam.

- [ ] **Step 6: Rodar lint + build**

Run: `cd apps/web && pnpm lint && pnpm build`
Expected: sem erros novos, build passa (confirma que nenhum import/tipo ficou órfão).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/components/AgentFolder.tsx apps/web/src/components/KnowledgeBasePanel.tsx apps/web/__tests__/KnowledgeBasePanel.test.tsx
git commit -m "feat(web): base de conhecimento em árvore, 1 pasta por agente"
```

---

### Task 4: Frontend — simplificar `/agentes/[id]`

**Files:**
- Modify: `apps/web/src/components/AgentDetail.tsx`
- Test: `apps/web/__tests__/AgentDetail.test.tsx`

**Interfaces:**
- Consumes: nada (independente da Task 3 — só remove UI, não depende do componente novo).
- Produces: nada — última task do plano.

- [ ] **Step 1: Atualizar os testes**

Em `apps/web/__tests__/AgentDetail.test.tsx`, troque a função `mockLoad`:

```tsx
function mockLoad(overrides?: { attached?: unknown[]; all?: unknown[] }) {
  mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (!init && path === "agents") return { ok: true, json: async () => [AGENT] };
    if (!init && path === "agents/a1/knowledge-base-files") {
      return { ok: true, json: async () => overrides?.attached ?? [] };
    }
    if (!init && path === "knowledge-base/files") {
      return { ok: true, json: async () => overrides?.all ?? [] };
    }
    return { ok: true, json: async () => null };
  });
}
```

por:

```tsx
function mockLoad(overrides?: { attached?: unknown[] }) {
  mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (!init && path === "agents") return { ok: true, json: async () => [AGENT] };
    if (!init && path === "agents/a1/knowledge-base-files") {
      return { ok: true, json: async () => overrides?.attached ?? [] };
    }
    return { ok: true, json: async () => null };
  });
}
```

Remova os testes `"lista os arquivos anexados e omite eles do seletor de anexar"` e `"desanexa um arquivo após confirmação"` (funcionalidade removida). Adicione, no lugar:

```tsx
  it("mostra a contagem de arquivos anexados com link pra base de conhecimento", async () => {
    mockLoad({ attached: [{ id: "f1", filename: "regimento.pdf", status: "ready" }] });

    render(<AgentDetail agentId="a1" />);

    await waitFor(() => expect(screen.getByText(/1 arquivo anexado/)).toBeInTheDocument());
    expect(
      screen.getByRole("link", { name: /gerenciar na base de conhecimento/ }),
    ).toHaveAttribute("href", "/base-de-conhecimento?agent_id=a1");
  });

  it("mostra plural quando há mais de um arquivo anexado", async () => {
    mockLoad({
      attached: [
        { id: "f1", filename: "regimento.pdf", status: "ready" },
        { id: "f2", filename: "modelo.docx", status: "ready" },
      ],
    });

    render(<AgentDetail agentId="a1" />);

    await waitFor(() => expect(screen.getByText(/2 arquivos anexados/)).toBeInTheDocument());
  });
```

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `cd apps/web && pnpm vitest run __tests__/AgentDetail.test.tsx`
Expected: FAIL — os 2 testes novos falham (o componente ainda renderiza a UI antiga de attach/detach, sem o texto de contagem).

- [ ] **Step 3: Simplificar `AgentDetail.tsx`**

Em `apps/web/src/components/AgentDetail.tsx`, remova os tipos/estados não mais usados. Troque:

```tsx
export function AgentDetail({ agentId }: { agentId: string }) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const [allFiles, setAllFiles] = useState<AttachedFile[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [saving, setSaving] = useState(false);
  const [selectedFileId, setSelectedFileId] = useState("");
  const [attaching, setAttaching] = useState(false);

  async function load() {
    try {
      const [agentsResponse, attachedResponse, allFilesResponse] = await Promise.all([
        backendFetch("agents"),
        backendFetch(`agents/${agentId}/knowledge-base-files`),
        backendFetch("knowledge-base/files"),
      ]);
      if (agentsResponse.ok) {
        const agents: Agent[] = await agentsResponse.json();
        const found = agents.find((a) => a.id === agentId) ?? null;
        setAgent(found);
        if (found) {
          setName(found.name);
          setInstructions(found.instructions);
        }
      }
      if (attachedResponse.ok) {
        setAttachedFiles(await attachedResponse.json());
      }
      if (allFilesResponse.ok) {
        setAllFiles(await allFilesResponse.json());
      }
    } finally {
      setLoaded(true);
    }
  }
```

por:

```tsx
export function AgentDetail({ agentId }: { agentId: string }) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      const [agentsResponse, attachedResponse] = await Promise.all([
        backendFetch("agents"),
        backendFetch(`agents/${agentId}/knowledge-base-files`),
      ]);
      if (agentsResponse.ok) {
        const agents: Agent[] = await agentsResponse.json();
        const found = agents.find((a) => a.id === agentId) ?? null;
        setAgent(found);
        if (found) {
          setName(found.name);
          setInstructions(found.instructions);
        }
      }
      if (attachedResponse.ok) {
        setAttachedFiles(await attachedResponse.json());
      }
    } finally {
      setLoaded(true);
    }
  }
```

Remova as funções `handleAttach` e `handleDetach` inteiras (ficam entre `handleSave` e o bloco `if (!loaded)`):

```tsx
  async function handleAttach(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedFileId) return;
    setFeedback(null);
    setAttaching(true);
    try {
      const response = await backendFetch(`agents/${agentId}/knowledge-base-files`, {
        method: "POST",
        body: JSON.stringify({ knowledge_base_file_id: selectedFileId }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(extractErrorDetail(body, "Falha ao anexar — tente novamente."));
        return;
      }
      setSelectedFileId("");
      await load();
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setAttaching(false);
    }
  }

  async function handleDetach(file: AttachedFile) {
    if (!window.confirm(`Desanexar "${file.filename}" deste agente?`)) return;
    try {
      const response = await backendFetch(`agents/${agentId}/knowledge-base-files/${file.id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(extractErrorDetail(body, "Falha ao desanexar — tente novamente."));
        return;
      }
      setAttachedFiles(attachedFiles.filter((f) => f.id !== file.id));
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    }
  }
```

Remova a linha (antes do `return` do JSX principal):

```tsx
  const attachedIds = new Set(attachedFiles.map((f) => f.id));
  const availableFiles = allFiles.filter((f) => !attachedIds.has(f.id));
```

Troque toda a seção de base de conhecimento no JSX:

```tsx
        <h2 className="font-display text-lg font-semibold text-ink">Base de conhecimento</h2>
        <ul className="mt-4 max-w-md">
          {attachedFiles.length === 0 && (
            <li className="py-4 text-sm text-muted">Nenhum arquivo anexado ainda.</li>
          )}
          {attachedFiles.map((file) => (
            <li
              key={file.id}
              className="flex items-center justify-between border-b border-line py-3"
            >
              <p className="truncate text-ink">{file.filename}</p>
              <button
                type="button"
                onClick={() => void handleDetach(file)}
                className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
              >
                Desanexar
              </button>
            </li>
          ))}
        </ul>

        <form onSubmit={handleAttach} className="mt-4 flex max-w-md items-end gap-2">
          <label className="flex flex-1 flex-col gap-1 text-sm text-ink">
            Anexar arquivo já enviado
            <select
              value={selectedFileId}
              onChange={(event) => setSelectedFileId(event.target.value)}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            >
              <option value="">Selecione um arquivo</option>
              {availableFiles.map((file) => (
                <option key={file.id} value={file.id}>
                  {file.filename}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            disabled={attaching || !selectedFileId}
            className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
          >
            {attaching ? "Anexando..." : "Anexar"}
          </button>
        </form>

        <p className="mt-4 text-sm text-muted">
          Ou{" "}
          <Link
            href={`/base-de-conhecimento?agent_id=${agent.id}`}
            className="text-accent hover:underline"
          >
            envie um arquivo novo direto pra este agente
          </Link>
          .
        </p>
```

por:

```tsx
        <h2 className="font-display text-lg font-semibold text-ink">Base de conhecimento</h2>
        <p className="mt-4 max-w-md text-sm text-muted">
          {attachedFiles.length} arquivo{attachedFiles.length === 1 ? "" : "s"} anexado
          {attachedFiles.length === 1 ? "" : "s"} —{" "}
          <Link
            href={`/base-de-conhecimento?agent_id=${agent.id}`}
            className="text-accent hover:underline"
          >
            gerenciar na base de conhecimento
          </Link>
          .
        </p>
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `cd apps/web && pnpm vitest run __tests__/AgentDetail.test.tsx`
Expected: todos os testes do arquivo passam.

- [ ] **Step 5: Rodar lint + build**

Run: `cd apps/web && pnpm lint && pnpm build`
Expected: sem erros novos — o build confirma que `handleAttach`/`handleDetach`/`selectedFileId`/`attaching`/`allFiles`/`availableFiles`/`attachedIds`/`FormEvent` (se ficar sem uso) não sobraram órfãos.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/AgentDetail.tsx apps/web/__tests__/AgentDetail.test.tsx
git commit -m "feat(web): /agentes/[id] mostra só um resumo somente-leitura da base de conhecimento"
```
