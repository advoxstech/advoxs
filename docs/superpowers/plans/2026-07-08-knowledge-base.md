# Gestão da Base de Conhecimento — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload/listagem/exclusão de documentos da base de conhecimento por tenant (`api` + `web`), ingestão assíncrona via `worker` → `api_rag`, e tool nos `agents` para consultar essa base.

**Architecture:** O `api` recebe o upload, grava o arquivo no volume compartilhado `kb_uploads`, registra em `knowledge_base_files` (`processing`) e enfileira no Arq após o commit. O `worker` lê o arquivo, envia ao `api_rag` (`/documents/users/insert` com `conversation_id="kb"` reservado e `doc_id` = id do registro) e marca `ready`/`error`. O front espelha o padrão do `/conversas`. Spec: `docs/superpowers/specs/2026-07-08-knowledge-base-design.md`.

**Tech Stack:** FastAPI + SQLAlchemy async + Arq (api/worker), httpx, Next.js 15 App Router (web), LangChain tools (agents), pytest + Vitest.

## Global Constraints

- Formatos aceitos: `.pdf` (`application/pdf`), `.docx` (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`), `.txt` (`text/plain`). Mime genérico (`application/octet-stream` ou vazio) é aceito; a extensão do filename é a fonte da verdade.
- Limites: 20 MB por arquivo (`KB_MAX_FILE_SIZE_BYTES`, default `20971520`), 500 MB por tenant (`KB_MAX_TOTAL_SIZE_BYTES`, default `524288000`).
- `conversation_id` reservado da KB no `api_rag`: a string literal `"kb"`.
- `doc_id` no `api_rag` = `knowledge_base_files.id` (mesmo UUID nos dois serviços).
- Códigos de erro do upload: 400 formato/mime/arquivo vazio, 413 tamanho/storage, 409 nome duplicado. Delete: 404 inexistente, 409 durante `processing`, 502 se o `api_rag` falhar.
- Volume compartilhado: `kb_uploads` montado em `/data/kb_uploads` no `api` e no `worker`; arquivo temporário em `{KB_UPLOAD_DIR}/{tenant_id}/{file_id}` (sem extensão).
- Mensagens de erro voltadas ao usuário em pt-BR, com acentuação correta.
- Commits: Conventional Commits em pt-BR (padrão do repo). Lint: `uv run ruff check .` (Python), `pnpm lint` (web).
- Testes Python rodam com `uv run pytest tests/unit` dentro de cada app (os `conftest.py` já setam envs fake).

---

### Task 1: `api_rag` — TXT + `doc_id` externo idempotente

**Files:**
- Modify: `apps/api_rag/services/documents/main.py`
- Modify: `apps/api_rag/api/routes/documents/users.py`
- Modify: `apps/api_rag/API.md`
- Test: `apps/api_rag/tests/unit/test_documento_service.py`

**Interfaces:**
- Produces: `POST /documents/users/insert` passa a aceitar form field opcional `doc_id` (UUID string). Quando presente: (a) se já existir documento com esse id, ele é deletado antes (re-ingestão idempotente para retries do worker); (b) o novo `DocumentoUsuario` usa esse id como PK e ele vai no payload do Qdrant. `_extrair_texto` passa a aceitar `extensao == "txt"`.
- Consumes: nada de tasks anteriores.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `apps/api_rag/tests/unit/test_documento_service.py` (seguir imports já existentes no arquivo; adicionar os que faltarem):

```python
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.documents.main import DocumentoService


def _service() -> DocumentoService:
    return DocumentoService(qdrant=MagicMock(), repo=MagicMock())


class TestExtrairTextoTxt:
    def test_txt_utf8(self) -> None:
        texto = _service()._extrair_texto("ação e direção".encode("utf-8"), "txt")
        assert texto == "ação e direção"

    def test_txt_latin1_fallback(self) -> None:
        texto = _service()._extrair_texto("ação".encode("latin-1"), "txt")
        assert texto == "ação"


class TestInserirComDocIdExterno:
    @pytest.fixture
    def service(self, monkeypatch) -> DocumentoService:
        service = DocumentoService(qdrant=MagicMock(), repo=AsyncMock())
        monkeypatch.setattr(
            service,
            "_processar_documento",
            AsyncMock(return_value=("txt", "texto", ["texto"], [[0.1]], [{"indices": [0], "values": [1.0]}])),
        )
        monkeypatch.setattr(service, "_salvar_arquivo", MagicMock(return_value=("/base", "path")))
        monkeypatch.setattr(service, "_salvar_qdrant", AsyncMock())
        monkeypatch.setattr(service, "deletar_documento_usuario", AsyncMock())
        return service

    def _file(self) -> MagicMock:
        file = MagicMock()
        file.filename = "regimento.txt"
        file.read = AsyncMock(return_value=b"conteudo")
        return file

    async def test_usa_doc_id_como_pk(self, service) -> None:
        doc_id = str(uuid.uuid4())
        service.repo.buscar_documento_usuario_por_id.return_value = None

        await service.inserir_documento_usuario([self._file()], "t1", "kb", doc_id=doc_id)

        instance = service.repo.criar_documento_usuario.await_args.args[0]
        assert str(instance.id) == doc_id
        service.deletar_documento_usuario.assert_not_awaited()

    async def test_doc_id_repetido_deleta_antes(self, service) -> None:
        doc_id = str(uuid.uuid4())
        service.repo.buscar_documento_usuario_por_id.return_value = MagicMock()

        await service.inserir_documento_usuario([self._file()], "t1", "kb", doc_id=doc_id)

        service.deletar_documento_usuario.assert_awaited_once_with("t1", [doc_id])

    async def test_sem_doc_id_mantem_default(self, service) -> None:
        service.repo.buscar_documento_usuario_por_id.return_value = None

        await service.inserir_documento_usuario([self._file()], "t1", "conversa-1")

        service.repo.buscar_documento_usuario_por_id.assert_not_awaited()
```

Se o arquivo de teste usar `pytest.mark.asyncio` explícito em vez de asyncio_mode auto, seguir o padrão local nos testes async novos.

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd apps/api_rag && uv run pytest tests/unit/test_documento_service.py -v -k "Txt or DocId"`
Expected: FAIL — `ValueError: Formato não suportado: txt` e `TypeError: inserir_documento_usuario() got an unexpected keyword argument 'doc_id'`.

- [ ] **Step 3: Implementar no service**

Em `apps/api_rag/services/documents/main.py`:

3a. `_extrair_texto` — adicionar o branch de txt antes do `else`:

```python
        elif extensao == "txt":
            try:
                return document_bytes.decode("utf-8")
            except UnicodeDecodeError:
                return document_bytes.decode("latin-1")
```

3b. `inserir_documento_usuario` — nova assinatura e uso do `doc_id`:

```python
    async def inserir_documento_usuario(
        self,
        files: list[UploadFile],
        tenant_id: str,
        conversation_id: str,
        doc_id: str | None = None,
    ):
```

Dentro do loop `for doc_item in list_documents_bytes:`, antes de criar a `instance`, adicionar:

```python
            if doc_id is not None:
                # Re-ingestão idempotente: retry do worker com o mesmo doc_id
                # substitui o documento anterior (disco + Qdrant + Postgres).
                existente = await self.repo.buscar_documento_usuario_por_id(UUID(doc_id))
                if existente is not None:
                    await self.deletar_documento_usuario(tenant_id, [doc_id])
```

E na criação da instância, usar o id externo quando presente:

```python
            instance = DocumentoUsuario(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                nome=doc_item["file_name"],
                extensao=extensao,
                path_base=path_base,
                path_doc=path_doc,
            )
            if doc_id is not None:
                instance.id = UUID(doc_id)
```

(O payload do Qdrant já usa `"doc_id": str(doc.id)` — nada a mudar lá.)

- [ ] **Step 4: Implementar na rota**

Em `apps/api_rag/api/routes/documents/users.py`, no endpoint `inserir_documento`:

```python
@router_doc_users.post("/insert")
async def inserir_documento(
    tenant_id: str = Form(...),
    conversation_id: str = Form(...),
    doc_id: str | None = Form(default=None),
    file: UploadFile = File(...),
    service: DocumentoService = Depends(get_service),
    security: str = Depends(verify_api_key),
):
    try:
        files = [file]
        logger.info(f"Recebendo {len(files)} arquivos | tenant={tenant_id}")
        await service.inserir_documento_usuario(files, tenant_id, conversation_id, doc_id=doc_id)
        return {"mensagem": "Documentos inseridos com sucesso"}
```

(blocos `except` inalterados).

- [ ] **Step 5: Rodar os testes e lint**

Run: `cd apps/api_rag && uv run pytest tests/unit -q && uv run ruff check .`
Expected: todos os testes PASS (inclusive os pré-existentes), ruff sem erros.

- [ ] **Step 6: Atualizar `API.md`**

Na seção do endpoint `POST /documents/users/insert` de `apps/api_rag/API.md`: documentar o form field opcional `doc_id` (UUID usado como PK e chave de deleção; re-ingestão com o mesmo `doc_id` substitui o documento) e adicionar `.txt` à lista de formatos aceitos. Registrar também o `conversation_id` reservado `"kb"` (base de conhecimento do escritório, gerida pelo `api`/`worker` do monorepo).

- [ ] **Step 7: Commit**

```bash
git add apps/api_rag
git commit -m "feat(api_rag): ingestão de txt e doc_id externo idempotente"
```

---

### Task 2: `api` — router `/api/v1/knowledge-base`

**Files:**
- Modify: `apps/api/app/core/config.py`
- Create: `apps/api/app/schemas/knowledge_base.py`
- Create: `apps/api/app/clients/rag.py`
- Create: `apps/api/app/api/v1/knowledge_base.py`
- Modify: `apps/api/app/api/v1/router.py`
- Test: `apps/api/tests/unit/test_knowledge_base_routes.py`

**Interfaces:**
- Consumes: contrato do `api_rag` da Task 1 (`DELETE /documents/users/delete?tenant_id=&docs_ids=`, header `Authorization: <RAG_API_KEY>` sem `Bearer`).
- Produces: `POST /api/v1/knowledge-base/files` (multipart, campo `file`) → 202 + `KnowledgeBaseFileOut`; `GET /api/v1/knowledge-base/files` → `list[KnowledgeBaseFileOut]`; `DELETE /api/v1/knowledge-base/files/{file_id}` → 204. Job enfileirado: `enqueue_job("ingest_knowledge_base_file", tenant_id=str, file_id=str)`. `KnowledgeBaseFileOut` = `{id: UUID, filename: str, size_bytes: int, mime_type: str, status: str, error_message: str | None, uploaded_at: datetime}`.

- [ ] **Step 1: Config**

Em `apps/api/app/core/config.py`, adicionar ao `Settings` (depois do bloco Graph API):

```python
    # Base de conhecimento (upload → volume compartilhado → ingestão no api_rag)
    kb_upload_dir: str = "/data/kb_uploads"
    kb_max_file_size_bytes: int = 20 * 1024 * 1024
    kb_max_total_size_bytes: int = 500 * 1024 * 1024
    rag_api_url: str = "http://api_rag:8000"
    rag_api_key: str = ""
```

- [ ] **Step 2: Escrever os testes que falham**

Criar `apps/api/tests/unit/test_knowledge_base_routes.py`:

```python
import io
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.knowledge_base as kb_module
from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.rag import RagApiError
from app.core.config import settings
from app.core.queue import get_arq_pool
from app.main import app

TENANT_ID = uuid.uuid4()
FILE_ID = uuid.uuid4()


def _record(status: str = "ready") -> SimpleNamespace:
    return SimpleNamespace(
        id=FILE_ID,
        tenant_id=TENANT_ID,
        filename="regimento.pdf",
        size_bytes=1000,
        mime_type="application/pdf",
        status=status,
        error_message=None,
        uploaded_at=datetime.now(UTC),
    )


@pytest.fixture
def session():
    mock = AsyncMock()
    mock.add = MagicMock()

    async def fake_refresh(obj):
        obj.uploaded_at = datetime.now(UTC)

    mock.refresh.side_effect = fake_refresh
    return mock


@pytest.fixture
def arq():
    return AsyncMock()


@pytest.fixture
def client(session, arq, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "kb_upload_dir", str(tmp_path))

    async def override_ctx():
        return TenantContext(user_id=uuid.uuid4(), tenant_id=TENANT_ID, role="admin")

    async def override_session():
        yield session

    async def override_arq():
        return arq

    app.dependency_overrides[get_current_tenant] = override_ctx
    app.dependency_overrides[get_tenant_session] = override_session
    app.dependency_overrides[get_arq_pool] = override_arq
    yield TestClient(app)
    app.dependency_overrides.clear()


def _upload(client, filename="regimento.pdf", content=b"%PDF-1.4 conteudo", mime="application/pdf"):
    return client.post(
        "/api/v1/knowledge-base/files",
        files={"file": (filename, io.BytesIO(content), mime)},
    )


def test_sem_token_retorna_401() -> None:
    response = TestClient(app).get("/api/v1/knowledge-base/files")

    assert response.status_code == 401


class TestUpload:
    def test_upload_feliz_enfileira_apos_commit(self, client, session, arq, tmp_path) -> None:
        # 1ª scalar: soma do storage usado; 2ª: checagem de duplicado.
        session.scalar.side_effect = [0, None]

        response = _upload(client)

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "processing"
        assert body["filename"] == "regimento.pdf"
        session.commit.assert_awaited()
        arq.enqueue_job.assert_awaited_once()
        kwargs = arq.enqueue_job.await_args.kwargs
        assert kwargs["tenant_id"] == str(TENANT_ID)
        saved = tmp_path / str(TENANT_ID) / kwargs["file_id"]
        assert saved.read_bytes() == b"%PDF-1.4 conteudo"

    def test_extensao_invalida_400(self, client) -> None:
        response = _upload(client, filename="malware.exe", mime="application/octet-stream")

        assert response.status_code == 400

    def test_mime_incompativel_400(self, client) -> None:
        response = _upload(client, filename="regimento.pdf", mime="text/plain")

        assert response.status_code == 400

    def test_arquivo_vazio_400(self, client, session) -> None:
        session.scalar.side_effect = [0, None]

        response = _upload(client, content=b"")

        assert response.status_code == 400

    def test_arquivo_grande_413(self, client, monkeypatch) -> None:
        monkeypatch.setattr(settings, "kb_max_file_size_bytes", 10)

        response = _upload(client, content=b"x" * 11)

        assert response.status_code == 413

    def test_storage_estourado_413(self, client, session, monkeypatch) -> None:
        monkeypatch.setattr(settings, "kb_max_total_size_bytes", 100)
        session.scalar.side_effect = [95]

        response = _upload(client, content=b"x" * 10)

        assert response.status_code == 413
        assert "restam" in response.json()["detail"]

    def test_nome_duplicado_409(self, client, session) -> None:
        session.scalar.side_effect = [0, FILE_ID]

        response = _upload(client)

        assert response.status_code == 409


class TestList:
    def test_lista_arquivos_do_tenant(self, client, session) -> None:
        result = MagicMock()
        result.scalars.return_value.all.return_value = [_record()]
        session.execute.return_value = result

        response = client.get("/api/v1/knowledge-base/files")

        assert response.status_code == 200
        assert response.json()[0]["filename"] == "regimento.pdf"


class TestDelete:
    @pytest.fixture
    def rag_delete(self, monkeypatch):
        mock = AsyncMock()
        monkeypatch.setattr(kb_module, "delete_documents", mock)
        return mock

    def test_delete_feliz_204(self, client, session, rag_delete) -> None:
        session.scalar.return_value = _record(status="ready")

        response = client.delete(f"/api/v1/knowledge-base/files/{FILE_ID}")

        assert response.status_code == 204
        rag_delete.assert_awaited_once_with(str(TENANT_ID), [str(FILE_ID)])
        session.delete.assert_awaited_once()

    def test_delete_durante_processing_409(self, client, session, rag_delete) -> None:
        session.scalar.return_value = _record(status="processing")

        response = client.delete(f"/api/v1/knowledge-base/files/{FILE_ID}")

        assert response.status_code == 409
        rag_delete.assert_not_awaited()

    def test_delete_inexistente_404(self, client, session, rag_delete) -> None:
        session.scalar.return_value = None

        response = client.delete(f"/api/v1/knowledge-base/files/{uuid.uuid4()}")

        assert response.status_code == 404

    def test_rag_indisponivel_502(self, client, session, rag_delete) -> None:
        session.scalar.return_value = _record(status="ready")
        rag_delete.side_effect = RagApiError("api_rag indisponível")

        response = client.delete(f"/api/v1/knowledge-base/files/{FILE_ID}")

        assert response.status_code == 502
        session.delete.assert_not_awaited()
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `cd apps/api && uv run pytest tests/unit/test_knowledge_base_routes.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'app.api.v1.knowledge_base'`.

- [ ] **Step 4: Schema**

Criar `apps/api/app/schemas/knowledge_base.py`:

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

- [ ] **Step 5: Client do api_rag**

Criar `apps/api/app/clients/rag.py`:

```python
"""Client do api_rag (serviço interno, API key única — nunca exposto ao escritório)."""

import httpx

from app.core.config import settings


class RagApiError(Exception):
    """Falha de comunicação ou resposta de erro do api_rag."""


async def delete_documents(tenant_id: str, doc_ids: list[str]) -> None:
    """Remove documentos no api_rag (disco + Qdrant + Postgres de lá).

    Idempotente do lado do api_rag: ids inexistentes são ignorados.
    """
    try:
        async with httpx.AsyncClient(base_url=settings.rag_api_url, timeout=30) as client:
            response = await client.delete(
                "/documents/users/delete",
                params={"tenant_id": tenant_id, "docs_ids": doc_ids},
                headers={"Authorization": settings.rag_api_key},
            )
    except httpx.HTTPError as exc:
        raise RagApiError(f"api_rag indisponível: {exc}") from exc
    if response.status_code != 200:
        raise RagApiError(f"api_rag retornou HTTP {response.status_code}")
```

- [ ] **Step 6: Router**

Criar `apps/api/app/api/v1/knowledge_base.py`:

```python
"""Base de conhecimento do escritório: upload, listagem e exclusão de arquivos."""

import uuid
from pathlib import Path

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.clients.rag import RagApiError, delete_documents
from app.core.config import settings
from app.core.queue import get_arq_pool
from app.models import KnowledgeBaseFile
from app.schemas.knowledge_base import KnowledgeBaseFileOut

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])

# A extensão do filename é a fonte da verdade; o mime declarado só precisa
# ser compatível ou genérico.
ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}
GENERIC_MIME_TYPES = {"", "application/octet-stream"}


@router.post("/files", status_code=status.HTTP_202_ACCEPTED)
async def upload_file(
    file: UploadFile = File(...),
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
    arq: ArqRedis = Depends(get_arq_pool),
) -> KnowledgeBaseFileOut:
    filename = file.filename or ""
    extension = Path(filename).suffix.lower()
    expected_mime = ALLOWED_EXTENSIONS.get(extension)
    if expected_mime is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato não suportado — envie PDF, DOCX ou TXT",
        )
    declared_mime = file.content_type or ""
    if declared_mime not in GENERIC_MIME_TYPES and declared_mime != expected_mime:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tipo de conteúdo não corresponde à extensão {extension}",
        )

    data = await file.read()
    if len(data) > settings.kb_max_file_size_bytes:
        limite_mb = settings.kb_max_file_size_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Arquivo excede o limite de {limite_mb} MB",
        )

    used = await session.scalar(
        select(func.coalesce(func.sum(KnowledgeBaseFile.size_bytes), 0)).where(
            KnowledgeBaseFile.tenant_id == ctx.tenant_id
        )
    )
    if used + len(data) > settings.kb_max_total_size_bytes:
        remaining = max(settings.kb_max_total_size_bytes - used, 0)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Limite de storage do escritório atingido — restam {remaining} bytes",
        )

    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Arquivo vazio")

    duplicate = await session.scalar(
        select(KnowledgeBaseFile.id).where(
            KnowledgeBaseFile.tenant_id == ctx.tenant_id,
            KnowledgeBaseFile.filename == filename,
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe um arquivo com esse nome — exclua o antigo antes de re-subir",
        )

    record = KnowledgeBaseFile(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        filename=filename,
        size_bytes=len(data),
        mime_type=expected_mime,
        status="processing",
    )
    session.add(record)

    tenant_dir = Path(settings.kb_upload_dir) / str(ctx.tenant_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    (tenant_dir / str(record.id)).write_bytes(data)

    await session.commit()
    await session.refresh(record)
    # Enfileira só depois do commit — o worker não pode acordar antes de a
    # linha estar visível (mesmo padrão do webhook do WhatsApp).
    await arq.enqueue_job(
        "ingest_knowledge_base_file",
        tenant_id=str(ctx.tenant_id),
        file_id=str(record.id),
    )
    return KnowledgeBaseFileOut.model_validate(record)


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


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: uuid.UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    record = await session.scalar(
        select(KnowledgeBaseFile).where(
            KnowledgeBaseFile.id == file_id,
            KnowledgeBaseFile.tenant_id == ctx.tenant_id,
        )
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arquivo não encontrado")
    if record.status == "processing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Arquivo em processamento — aguarde a ingestão terminar para excluir",
        )

    # Remove no api_rag primeiro: se falhar, o registro fica e o usuário
    # tenta de novo (nunca deixa chunk órfão no Qdrant).
    try:
        await delete_documents(str(ctx.tenant_id), [str(file_id)])
    except RagApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    temp_path = Path(settings.kb_upload_dir) / str(ctx.tenant_id) / str(file_id)
    temp_path.unlink(missing_ok=True)

    await session.delete(record)
    await session.commit()
```

Nota sobre a ordem das validações no upload: o teste `test_arquivo_vazio_400` espera 400 para bytes vazios; a checagem de `not data` está depois da soma de storage para casar com o `side_effect` dos mocks — manter essa ordem.

- [ ] **Step 7: Registrar no router**

Em `apps/api/app/api/v1/router.py`:

```python
from app.api.v1.knowledge_base import router as knowledge_base_router
```

e, junto aos outros includes:

```python
api_router.include_router(knowledge_base_router)
```

- [ ] **Step 8: Rodar os testes e lint**

Run: `cd apps/api && uv run pytest tests/unit -q && uv run ruff check .`
Expected: todos PASS, ruff limpo.

- [ ] **Step 9: Commit**

```bash
git add apps/api
git commit -m "feat(api): rotas de gestão da base de conhecimento (upload, listagem, exclusão)"
```

---

### Task 3: `worker` — implementação de `ingest_knowledge_base_file`

**Files:**
- Modify: `apps/worker/app/config.py`
- Modify: `apps/worker/app/tables.py`
- Create: `apps/worker/app/clients/rag.py`
- Modify: `apps/worker/app/tasks/knowledge_base.py`
- Modify: `apps/worker/app/worker.py`
- Test: `apps/worker/tests/unit/test_ingest_knowledge_base_file.py`

**Interfaces:**
- Consumes: job `ingest_knowledge_base_file(ctx, tenant_id: str, file_id: str)` enfileirado pela Task 2; contrato de insert do `api_rag` da Task 1; arquivo em `{kb_upload_dir}/{tenant_id}/{file_id}`.
- Produces: atualização de `knowledge_base_files.status` → `ready` | `error` (+ `error_message`); `ctx["rag_http"]` (`httpx.AsyncClient` com `base_url=settings.rag_api_url`).

- [ ] **Step 1: Config e tabela**

Em `apps/worker/app/config.py`, adicionar ao `Settings`:

```python
    # api_rag (ingestão da base de conhecimento)
    rag_api_url: str = "http://api_rag:8000"
    rag_api_key: str = ""
    kb_upload_dir: str = "/data/kb_uploads"
```

Em `apps/worker/app/tables.py`, adicionar ao final:

```python
knowledge_base_files = Table(
    "knowledge_base_files",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", Uuid),
    Column("filename", String),
    Column("status", String),
    Column("error_message", Text),
)
```

- [ ] **Step 2: Escrever os testes que falham**

Criar `apps/worker/tests/unit/test_ingest_knowledge_base_file.py`:

```python
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from arq.worker import Retry

from app.config import settings
from app.tasks import knowledge_base as kb_task
from app.tasks.knowledge_base import ingest_knowledge_base_file

TENANT_ID = str(uuid.uuid4())
FILE_ID = str(uuid.uuid4())


def _ctx(job_try: int = 1) -> dict:
    session = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return {"session_factory": factory, "rag_http": AsyncMock(), "job_try": job_try, "_session": session}


def _row(status: str = "processing") -> SimpleNamespace:
    return SimpleNamespace(filename="regimento.pdf", status=status)


@pytest.fixture
def temp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "kb_upload_dir", str(tmp_path))
    tenant_dir = tmp_path / TENANT_ID
    tenant_dir.mkdir(parents=True)
    path = tenant_dir / FILE_ID
    path.write_bytes(b"%PDF-1.4 conteudo")
    return path


@pytest.fixture
def patched(monkeypatch):
    mocks = {
        "load": AsyncMock(return_value=_row()),
        "ingest": AsyncMock(),
        "set_status": AsyncMock(),
    }
    monkeypatch.setattr(kb_task, "_load_file", mocks["load"])
    monkeypatch.setattr(kb_task, "ingest_document", mocks["ingest"])
    monkeypatch.setattr(kb_task, "_set_status", mocks["set_status"])
    return mocks


async def test_sucesso_marca_ready_e_apaga_temp(patched, temp_file) -> None:
    await ingest_knowledge_base_file(_ctx(), TENANT_ID, FILE_ID)

    kwargs = patched["ingest"].await_args.kwargs
    assert kwargs["tenant_id"] == TENANT_ID
    assert kwargs["doc_id"] == FILE_ID
    assert kwargs["filename"] == "regimento.pdf"
    assert kwargs["file_bytes"] == b"%PDF-1.4 conteudo"
    patched["set_status"].assert_awaited_once()
    assert patched["set_status"].await_args.args[2] == "ready"
    assert not temp_file.exists()


async def test_status_nao_processing_encerra(patched, temp_file) -> None:
    patched["load"].return_value = _row(status="ready")

    await ingest_knowledge_base_file(_ctx(), TENANT_ID, FILE_ID)

    patched["ingest"].assert_not_awaited()
    patched["set_status"].assert_not_awaited()


async def test_arquivo_sumido_marca_error(patched, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "kb_upload_dir", str(tmp_path))

    await ingest_knowledge_base_file(_ctx(), TENANT_ID, FILE_ID)

    patched["ingest"].assert_not_awaited()
    assert patched["set_status"].await_args.args[2] == "error"


async def test_erro_transiente_reagenda(patched, temp_file) -> None:
    patched["ingest"].side_effect = httpx.ConnectError("down")

    with pytest.raises(Retry):
        await ingest_knowledge_base_file(_ctx(job_try=1), TENANT_ID, FILE_ID)

    patched["set_status"].assert_not_awaited()


async def test_erro_transiente_na_ultima_tentativa_marca_error(patched, temp_file) -> None:
    patched["ingest"].side_effect = httpx.ConnectError("down")

    await ingest_knowledge_base_file(_ctx(job_try=5), TENANT_ID, FILE_ID)

    assert patched["set_status"].await_args.args[2] == "error"
    assert temp_file.exists()  # temp fica para eventual retry manual


async def test_erro_definitivo_4xx_marca_error(patched, temp_file) -> None:
    response = httpx.Response(400, request=httpx.Request("POST", "http://rag"), text="formato")
    patched["ingest"].side_effect = httpx.HTTPStatusError("400", request=response.request, response=response)

    await ingest_knowledge_base_file(_ctx(job_try=1), TENANT_ID, FILE_ID)

    args = patched["set_status"].await_args.args
    assert args[2] == "error"
    assert "400" in args[3]
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `cd apps/worker && uv run pytest tests/unit/test_ingest_knowledge_base_file.py -v`
Expected: FAIL — `AttributeError: module 'app.tasks.knowledge_base' has no attribute '_load_file'` (o stub não tem corpo).

- [ ] **Step 4: Client do api_rag**

Criar `apps/worker/app/clients/rag.py`:

```python
"""Client do api_rag — ingestão de documentos da base de conhecimento."""

import httpx

from app.config import settings

# conversation_id reservado da base de conhecimento do escritório (espelha
# o tenant reservado "system" da base da plataforma).
KB_CONVERSATION_ID = "kb"


async def ingest_document(
    http: httpx.AsyncClient,
    *,
    tenant_id: str,
    doc_id: str,
    filename: str,
    file_bytes: bytes,
) -> None:
    """Envia o arquivo ao api_rag. doc_id = id de knowledge_base_files.

    Levanta httpx.HTTPStatusError em resposta de erro (raise_for_status).
    """
    response = await http.post(
        "/documents/users/insert",
        data={"tenant_id": tenant_id, "conversation_id": KB_CONVERSATION_ID, "doc_id": doc_id},
        files={"file": (filename, file_bytes, "application/octet-stream")},
        headers={"Authorization": settings.rag_api_key},
    )
    response.raise_for_status()
```

- [ ] **Step 5: Task**

Substituir o conteúdo de `apps/worker/app/tasks/knowledge_base.py`:

```python
import logging
import uuid
from pathlib import Path

import httpx
from arq.worker import Retry
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import tables
from app.clients.rag import ingest_document
from app.config import settings

logger = logging.getLogger(__name__)

# Na última tentativa, marca error em vez de reagendar (o default de
# max_tries do Arq também é 5 — manter em sincronia).
MAX_TRIES = 5


async def ingest_knowledge_base_file(ctx: dict, tenant_id: str, file_id: str) -> None:
    """Lê o arquivo do volume compartilhado, ingere no api_rag e marca o status.

    Idempotente: retries re-checam o status antes de reprocessar, e o api_rag
    substitui documento re-ingerido com o mesmo doc_id.
    """
    session_factory = ctx["session_factory"]
    http: httpx.AsyncClient = ctx["rag_http"]

    async with session_factory() as session:
        row = await _load_file(session, file_id)

    if row is None or row.status != "processing":
        logger.info("Arquivo inexistente ou já processado | file=%s", file_id)
        return

    path = Path(settings.kb_upload_dir) / tenant_id / file_id
    if not path.exists():
        await _set_status(session_factory, file_id, "error", "Arquivo temporário não encontrado")
        return

    try:
        await ingest_document(
            http,
            tenant_id=tenant_id,
            doc_id=file_id,
            filename=row.filename,
            file_bytes=path.read_bytes(),
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500 and ctx.get("job_try", 1) < MAX_TRIES:
            logger.warning("api_rag 5xx, reagendando | file=%s", file_id)
            raise Retry(defer=ctx.get("job_try", 1) * 15)
        await _set_status(
            session_factory,
            file_id,
            "error",
            f"Falha na ingestão (HTTP {exc.response.status_code})",
        )
        return
    except httpx.HTTPError as exc:
        if ctx.get("job_try", 1) < MAX_TRIES:
            logger.warning("api_rag indisponível, reagendando | file=%s erro=%s", file_id, exc)
            raise Retry(defer=ctx.get("job_try", 1) * 15)
        await _set_status(session_factory, file_id, "error", "Serviço de ingestão indisponível")
        return

    await _set_status(session_factory, file_id, "ready", None)
    path.unlink(missing_ok=True)
    logger.info("Arquivo ingerido | tenant=%s file=%s", tenant_id, file_id)


async def _load_file(session: AsyncSession, file_id: str):
    return (
        await session.execute(
            select(
                tables.knowledge_base_files.c.filename,
                tables.knowledge_base_files.c.status,
            ).where(tables.knowledge_base_files.c.id == uuid.UUID(file_id))
        )
    ).one_or_none()


async def _set_status(
    session_factory, file_id: str, status: str, error_message: str | None
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(tables.knowledge_base_files)
            .where(tables.knowledge_base_files.c.id == uuid.UUID(file_id))
            .values(status=status, error_message=error_message)
        )
        await session.commit()
```

- [ ] **Step 6: Registrar o client no ctx do worker**

Em `apps/worker/app/worker.py`, no `startup`, após a criação de `ctx["http"]`:

```python
    # A ingestão do api_rag é síncrona (parsing + embeddings + Qdrant) — timeout largo.
    ctx["rag_http"] = httpx.AsyncClient(
        base_url=settings.rag_api_url, timeout=httpx.Timeout(300.0)
    )
```

E no `shutdown`, antes do dispose:

```python
    await ctx["rag_http"].aclose()
```

- [ ] **Step 7: Rodar os testes e lint**

Run: `cd apps/worker && uv run pytest tests/unit -q && uv run ruff check .`
Expected: todos PASS (o teste existente `test_worker_settings.py` continua passando — a função já estava registrada), ruff limpo.

- [ ] **Step 8: Commit**

```bash
git add apps/worker
git commit -m "feat(worker): ingestão de arquivo da base de conhecimento via api_rag"
```

---

### Task 4: Docker Compose — volume `kb_uploads` e envs

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: defaults `kb_upload_dir="/data/kb_uploads"` e `rag_api_url="http://api_rag:8000"` das Tasks 2 e 3.
- Produces: volume compartilhado visível pelo `api` e pelo `worker`; `RAG_API_KEY` já chega aos containers via `env_file: .env` (mesma variável que o `api_rag` consome como `API_KEY`).

- [ ] **Step 1: Editar o compose**

Em `docker-compose.yml`:

1a. Em `volumes:` (topo), adicionar:

```yaml
  kb_uploads:
```

1b. No serviço `api`, adicionar:

```yaml
    environment:
      RAG_API_URL: http://api_rag:8000
    volumes:
      - kb_uploads:/data/kb_uploads
```

e incluir `api_rag` no `depends_on` existente do `api` (que hoje tem `postgres` e `redis`).

1c. No serviço `worker`, adicionar:

```yaml
    environment:
      RAG_API_URL: http://api_rag:8000
    volumes:
      - kb_uploads:/data/kb_uploads
```

e incluir `api_rag` no `depends_on` existente do `worker`.

- [ ] **Step 2: Validar**

Run: `docker compose config --quiet && echo OK`
Expected: `OK` (sem erro de sintaxe/merge).

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(infra): volume kb_uploads compartilhado entre api e worker"
```

---

### Task 5: `web` — proxy com multipart e DELETE

**Files:**
- Modify: `apps/web/src/lib/backend.ts`
- Modify: `apps/web/src/app/api/backend/[...path]/route.ts`
- Modify: `apps/web/src/lib/client-api.ts`
- Test: `apps/web/__tests__/backend.test.ts`

**Interfaces:**
- Produces: `backendFetch(path, init)` aceita `FormData` como body (sem forçar `content-type: application/json`); o proxy repassa `DELETE` e corpos multipart para `knowledge-base/*`.
- Consumes: rotas da Task 2.

- [ ] **Step 1: Teste que falha (allowlist)**

Em `apps/web/__tests__/backend.test.ts`, adicionar (seguindo o padrão dos testes existentes no arquivo):

```ts
it("permite rotas de knowledge-base", () => {
  expect(isAllowedPath(["knowledge-base", "files"])).toBe(true);
});
```

Run: `cd apps/web && pnpm test -- backend`
Expected: FAIL — `knowledge-base` não está na allowlist.

- [ ] **Step 2: Allowlist**

Em `apps/web/src/lib/backend.ts`:

```ts
const ALLOWED_PREFIXES = ["conversations", "knowledge-base"];
```

Run: `cd apps/web && pnpm test -- backend` → PASS.

- [ ] **Step 3: Proxy — corpo binário, content-type original e DELETE**

Em `apps/web/src/app/api/backend/[...path]/route.ts`, substituir a leitura do body e o `forward` (linhas do `const body =` ao fechamento do `forward`):

```ts
  const contentType = request.headers.get("content-type");
  const hasBody = request.method !== "GET" && request.method !== "DELETE";
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
```

E no final do arquivo:

```ts
export { handle as GET, handle as POST, handle as PATCH, handle as DELETE };
```

(O `arrayBuffer` é lido uma vez para uma variável, então o retry pós-refresh de token continua funcionando. Repassar o `content-type` original preserva o `boundary` do multipart.)

- [ ] **Step 4: client-api — não forçar JSON em FormData**

Em `apps/web/src/lib/client-api.ts`:

```ts
"use client";

/** Fetch do browser via proxy autenticado; sessão expirada volta pro login. */
export async function backendFetch(path: string, init?: RequestInit): Promise<Response> {
  const isFormData = init?.body instanceof FormData;
  const response = await fetch(`/api/backend/${path}`, {
    ...init,
    headers: {
      ...(isFormData ? {} : { "content-type": "application/json" }),
      ...init?.headers,
    },
  });
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Sessão expirada");
  }
  return response;
}
```

(Com `FormData`, o browser define o `content-type` com boundary sozinho.)

- [ ] **Step 5: Testes, lint e build**

Run: `cd apps/web && pnpm test && pnpm lint && pnpm build`
Expected: tudo verde.

- [ ] **Step 6: Commit**

```bash
git add apps/web
git commit -m "feat(web): proxy backend com suporte a multipart e DELETE"
```

---

### Task 6: `web` — página `/base-de-conhecimento`

**Files:**
- Create: `apps/web/src/components/KnowledgeBasePanel.tsx`
- Create: `apps/web/src/app/base-de-conhecimento/page.tsx`
- Modify: `apps/web/src/app/conversas/page.tsx`
- Modify: `apps/web/src/middleware.ts`
- Test: `apps/web/__tests__/KnowledgeBasePanel.test.tsx`

**Interfaces:**
- Consumes: `backendFetch` da Task 5; rotas `knowledge-base/files` da Task 2; `logout` de `src/app/conversas/actions.ts`; tokens de design (`bg-surface`, `border-line`, `text-muted`, `brass`, `danger`, `font-display`, `font-mono`) de `globals.css`/`tailwind.config.ts`.
- Produces: rota `/base-de-conhecimento` protegida pelo middleware; tipo `KbFile` local ao componente.

- [ ] **Step 1: Teste que falha**

Criar `apps/web/__tests__/KnowledgeBasePanel.test.tsx` (usar os mesmos imports/setup de `ConversationList.test.tsx` — Testing Library):

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { KnowledgeBasePanel } from "@/components/KnowledgeBasePanel";

const files = [
  {
    id: "f1",
    filename: "regimento.pdf",
    size_bytes: 1048576,
    mime_type: "application/pdf",
    status: "ready",
    error_message: null,
    uploaded_at: "2026-07-08T12:00:00Z",
  },
  {
    id: "f2",
    filename: "contrato.docx",
    size_bytes: 2048,
    mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    status: "error",
    error_message: "Falha na ingestão (HTTP 400)",
    uploaded_at: "2026-07-08T11:00:00Z",
  },
];

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => files,
  })),
}));

describe("KnowledgeBasePanel", () => {
  it("lista os arquivos com status", async () => {
    render(<KnowledgeBasePanel pollMs={0} />);

    await waitFor(() => expect(screen.getByText("regimento.pdf")).toBeInTheDocument());
    expect(screen.getByText("contrato.docx")).toBeInTheDocument();
    expect(screen.getByText(/pronto/i)).toBeInTheDocument();
    expect(screen.getByText(/Falha na ingestão/)).toBeInTheDocument();
  });
});
```

Run: `cd apps/web && pnpm test -- KnowledgeBasePanel`
Expected: FAIL — componente não existe.

- [ ] **Step 2: Componente**

Criar `apps/web/src/components/KnowledgeBasePanel.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { backendFetch } from "@/lib/client-api";

type KbFile = {
  id: string;
  filename: string;
  size_bytes: number;
  mime_type: string;
  status: "processing" | "ready" | "error";
  error_message: string | null;
  uploaded_at: string;
};

const ACCEPTED = ".pdf,.docx,.txt";
const MAX_FILE_BYTES = 20 * 1024 * 1024;

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

export function KnowledgeBasePanel({ pollMs = 5000 }: { pollMs?: number }) {
  const [files, setFiles] = useState<KbFile[]>([]);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    const response = await backendFetch("knowledge-base/files");
    if (response.ok) {
      setFiles(await response.json());
    }
  }, []);

  useEffect(() => {
    void load();
    if (!pollMs) return;
    const interval = setInterval(() => void load(), pollMs);
    return () => clearInterval(interval);
  }, [load, pollMs]);

  async function handleUpload(selected: File) {
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
    setUploading(true);
    try {
      const response = await backendFetch("knowledge-base/files", {
        method: "POST",
        body: form,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(body?.detail ?? "Falha no upload — tente novamente.");
        return;
      }
      await load();
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function handleDelete(file: KbFile) {
    if (!window.confirm(`Excluir "${file.filename}" da base de conhecimento?`)) return;
    const response = await backendFetch(`knowledge-base/files/${file.id}`, { method: "DELETE" });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      setFeedback(body?.detail ?? "Falha ao excluir — tente novamente.");
      return;
    }
    await load();
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      <header className="flex items-center justify-between border-b border-line px-8 py-5">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">Base de conhecimento</h1>
          <p className="text-sm text-muted">
            PDF, DOCX ou TXT, até 20 MB — os agentes consultam esses documentos nas conversas.
          </p>
        </div>
        <label
          className={`cursor-pointer rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent ${uploading ? "pointer-events-none opacity-50" : ""}`}
        >
          {uploading ? "Enviando..." : "Enviar arquivo"}
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED}
            className="hidden"
            onChange={(event) => {
              const selected = event.target.files?.[0];
              if (selected) void handleUpload(selected);
            }}
          />
        </label>
      </header>

      {feedback && (
        <p className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">{feedback}</p>
      )}

      <ul className="flex-1 overflow-y-auto px-8 py-4">
        {files.length === 0 && (
          <li className="py-10 text-center text-sm text-muted">
            Nenhum arquivo na base de conhecimento ainda.
          </li>
        )}
        {files.map((file) => (
          <li
            key={file.id}
            className="flex items-center gap-4 border-b border-line py-4 last:border-b-0"
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
            <button
              type="button"
              onClick={() => void handleDelete(file)}
              disabled={file.status === "processing"}
              className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger disabled:opacity-40"
            >
              Excluir
            </button>
          </li>
        ))}
      </ul>
    </main>
  );
}
```

(Se alguma classe de token não existir — ex.: `accent-soft`, `brass-soft` —, conferir os nomes exatos em `tailwind.config.ts` e ajustar para os tokens reais.)

- [ ] **Step 3: Página e navegação**

Criar `apps/web/src/app/base-de-conhecimento/page.tsx`:

```tsx
import Link from "next/link";

import { KnowledgeBasePanel } from "@/components/KnowledgeBasePanel";

import { logout } from "../conversas/actions";

export default function BaseDeConhecimentoPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <nav className="flex w-14 shrink-0 flex-col items-center justify-between bg-ink py-5">
        <div className="flex flex-col items-center gap-6">
          <span className="font-display text-2xl font-semibold text-ground" aria-label="Advoxs">
            A.
          </span>
          <Link
            href="/conversas"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Conversas
          </Link>
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground [writing-mode:vertical-rl]">
            Base
          </span>
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
      <KnowledgeBasePanel />
    </div>
  );
}
```

Em `apps/web/src/app/conversas/page.tsx`, adicionar o link espelhado na nav (dentro do primeiro bloco, após o logo — envolver logo + links num `<div className="flex flex-col items-center gap-6">` como acima):

```tsx
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground [writing-mode:vertical-rl]">
            Conversas
          </span>
          <Link
            href="/base-de-conhecimento"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Base
          </Link>
```

(com `import Link from "next/link";` no topo).

- [ ] **Step 4: Middleware**

Em `apps/web/src/middleware.ts`:

```ts
export const config = {
  matcher: ["/", "/login", "/conversas/:path*", "/base-de-conhecimento/:path*"],
};
```

- [ ] **Step 5: Testes, lint e build**

Run: `cd apps/web && pnpm test && pnpm lint && pnpm build`
Expected: tudo verde (inclusive o teste do Step 1).

- [ ] **Step 6: Commit**

```bash
git add apps/web
git commit -m "feat(web): página de gestão da base de conhecimento"
```

---

### Task 7: `agents` — tool `buscar_base_conhecimento_escritorio`

**Files:**
- Modify: `apps/agents/clients/retrieval.py`
- Modify: `apps/agents/agents/tools.py`
- Modify: `apps/agents/agents/nodes.py`
- Modify: `apps/agents/agents/prompts/secretaria.md`, `condominial.md`, `contratos.md`, `direito_consumidor.md`
- Modify: `apps/agents/API_AGENTS.md`
- Test: `apps/agents/tests/unit/test_retrieval_client.py`, `apps/agents/tests/unit/test_nodes.py`

**Interfaces:**
- Consumes: `/retrieval/users` do `api_rag` (json `{tenant_id, conversation_id, message}`); documentos indexados com `conversation_id="kb"` pela Task 3; `state["conversation_id"]` = thread_id composto `"{tenant_id}:{contact_phone_number}"`.
- Produces: `retrieval_escritorio(conversation_id: str, message: str) -> list[dict]`; tool `buscar_base_conhecimento_escritorio(query, conversation_id)`; `tool_node` injeta `state["conversation_id"]` nas tools de retrieval (nunca confia no valor gerado pelo LLM).

- [ ] **Step 1: Testes que falham**

1a. Em `apps/agents/tests/unit/test_retrieval_client.py`, adicionar (adaptar imports/mocks ao padrão já usado no arquivo para `retrieval_usuario`):

```python
from unittest.mock import AsyncMock, MagicMock

import clients.retrieval as retrieval_module
from clients.retrieval import retrieval_escritorio


def _mock_async_client(monkeypatch, payload: dict) -> AsyncMock:
    client = AsyncMock()
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    client.post.return_value = response
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(retrieval_module.httpx, "AsyncClient", MagicMock(return_value=cm))
    return client


async def test_retrieval_escritorio_usa_conversation_id_kb(monkeypatch) -> None:
    client = _mock_async_client(monkeypatch, {"results": [{"text": "regimento"}]})

    results = await retrieval_escritorio("tenant-1:5511999998888", "qual o regimento?")

    assert results == [{"text": "regimento"}]
    body = client.post.await_args.kwargs["json"]
    assert body["tenant_id"] == "tenant-1"
    assert body["conversation_id"] == "kb"
    assert body["message"] == "qual o regimento?"
```

1b. Em `apps/agents/tests/unit/test_nodes.py`, adicionar (adaptar ao padrão do arquivo):

```python
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

import agents.tools as tools_module
from agents.nodes import tool_node


async def test_tool_node_injeta_conversation_id_do_estado(monkeypatch) -> None:
    retrieval = AsyncMock(return_value=[])
    monkeypatch.setattr(tools_module, "retrieval_escritorio", retrieval)

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "buscar_base_conhecimento_escritorio",
                # O LLM tentou passar outro id — deve ser ignorado.
                "args": {"query": "regimento", "conversation_id": "tenant-malicioso:123"},
                "id": "call-1",
            }
        ],
    )
    state = {"messages": [message], "conversation_id": "tenant-real:5511999998888"}

    await tool_node(state)

    retrieval.assert_awaited_once_with("tenant-real:5511999998888", "regimento")
```

Run: `cd apps/agents && uv run pytest tests/unit/test_retrieval_client.py tests/unit/test_nodes.py -v -k "escritorio or injeta"`
Expected: FAIL — `ImportError: cannot import name 'retrieval_escritorio'`.

- [ ] **Step 2: Client de retrieval**

Em `apps/agents/clients/retrieval.py`, adicionar após `retrieval_usuario`:

```python
# conversation_id reservado da base de conhecimento do escritório —
# documentos ingeridos pelo worker do monorepo com esse marcador.
KB_CONVERSATION_ID = "kb"


async def retrieval_escritorio(conversation_id: str, message: str) -> list[dict]:
    """Busca na base de conhecimento própria do escritório (tenant).

    Args:
        conversation_id: thread_id composto "{tenant_id}:{contact_phone_number}" —
            só o tenant_id é usado; a busca é sempre em conversation_id="kb".
        message: Pergunta do usuário.
    """
    tenant_id, _, _ = str(conversation_id).partition(":")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{RAG_API_URL}/retrieval/users",
                json={
                    "tenant_id": tenant_id,
                    "conversation_id": KB_CONVERSATION_ID,
                    "message": message,
                },
                headers=HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            logger.debug("Retrieval escritório retornou {} chunks | tenant={}", len(results), tenant_id)
            return results

    except httpx.HTTPStatusError as e:
        logger.error("Erro HTTP no retrieval escritório | status={} | response={}", e.response.status_code, e.response.text)
        return []
    except Exception as e:
        logger.error("Erro ao consultar retrieval escritório | error={}", str(e))
        return []
```

- [ ] **Step 3: Tool**

Em `apps/agents/agents/tools.py`:

3a. Atualizar o import: `from clients.retrieval import retrieval_sistema, retrieval_usuario, retrieval_escritorio`.

3b. Adicionar a tool (após `bucar_base_conhecimento_usuario`):

```python
@tool("buscar_base_conhecimento_escritorio")
async def buscar_base_conhecimento_escritorio(query: str, conversation_id: str) -> str:
    """Busca na base de conhecimento própria do escritório de advocacia.

    Use quando a pergunta envolver documentos, materiais, modelos ou
    orientações internas do próprio escritório — por exemplo regimentos,
    políticas de atendimento, modelos de contrato do escritório ou qualquer
    material institucional que o escritório tenha cadastrado na plataforma.

    Args:
        query: Pergunta ou tema a ser pesquisado nos documentos do escritório.
        conversation_id: ID da conversa (preenchido automaticamente pelo sistema).
    """
    return await retrieval_escritorio(conversation_id, query)
```

3c. Adicionar `buscar_base_conhecimento_escritorio` à lista `tools` no final do arquivo (antes de `transfer_to_specialist`).

- [ ] **Step 4: Nodes — bind e injeção de estado**

Em `apps/agents/agents/nodes.py`:

4a. Bind da tool nova nos quatro agentes:

- `agente_secretaria`: `model.bind_tools([transfer_to_specialist, buscar_base_conhecimento_escritorio])`
- `agente_condominial`: `model.bind_tools([transfer_to_specialist, bucar_base_conhecimento_condominial, bucar_base_conhecimento_usuario, buscar_base_conhecimento_escritorio])`
- `agente_contratos`: `model.bind_tools([transfer_to_specialist, bucar_base_conhecimento_contratos, bucar_base_conhecimento_usuario, buscar_base_conhecimento_escritorio])`
- `agente_direito_consumidor`: `model.bind_tools([transfer_to_specialist, bucar_base_conhecimento_direito_consumidor, bucar_base_conhecimento_usuario, buscar_base_conhecimento_escritorio])`

4b. No `tool_node`, injetar o `conversation_id` do estado (nunca confiar no gerado pelo LLM — o tenant_id vive dentro dele; isolamento multi-tenant). Logo acima do loop, adicionar a constante no topo do arquivo (após os imports):

```python
# Tools cujo conversation_id vem SEMPRE do estado do grafo, nunca do LLM —
# o tenant_id vive dentro dele (isolamento multi-tenant).
STATE_SCOPED_TOOLS = {"bucar_base_conhecimento_usuario", "buscar_base_conhecimento_escritorio"}
```

E dentro do loop `for tool_call in tool_calls:`, substituir a linha `observation = await tool.ainvoke(tool_call["args"])` por:

```python
        args = dict(tool_call["args"])
        if tool_call["name"] in STATE_SCOPED_TOOLS:
            args["conversation_id"] = state["conversation_id"]

        logger.info("Executando ferramenta | tool={} | args={}", tool_call["name"], args)
        observation = await tool.ainvoke(args)
```

(remover o `logger.info("Executando ferramenta ...")` antigo que usava `tool_call["args"]` para não duplicar o log).

- [ ] **Step 5: Rodar os testes e lint**

Run: `cd apps/agents && uv run pytest tests/unit -q && uv run ruff check .`
Expected: todos PASS (os testes existentes de nodes/tools continuam passando), ruff limpo.

- [ ] **Step 6: Prompts e documentação**

6a. Nos quatro prompts (`apps/agents/agents/prompts/secretaria.md`, `condominial.md`, `contratos.md`, `direito_consumidor.md`), adicionar ao final uma seção curta (mesmo tom do restante de cada prompt):

```markdown

## Base de conhecimento do escritório

Você tem acesso à ferramenta `buscar_base_conhecimento_escritorio`, que busca nos documentos que o próprio escritório cadastrou na plataforma (regimentos, políticas, modelos e materiais institucionais). Use-a quando a pergunta envolver informações específicas do escritório — antes de responder que não sabe algo sobre o escritório, consulte essa base.
```

6b. Em `apps/agents/API_AGENTS.md`, na seção de tools: documentar `buscar_base_conhecimento_escritorio` (busca na KB do tenant via `/retrieval/users` com `conversation_id="kb"`) e registrar que o `tool_node` injeta `conversation_id` do estado nas tools de retrieval escopadas (`STATE_SCOPED_TOOLS`).

- [ ] **Step 7: Commit**

```bash
git add apps/agents
git commit -m "feat(agents): tool de consulta à base de conhecimento do escritório"
```

---

### Task 8: Atualizar `CLAUDE.md` e verificação ponta a ponta

**Files:**
- Modify: `CLAUDE.md`
- (verificação) `docker compose` local

**Interfaces:**
- Consumes: tudo das tasks anteriores.

- [ ] **Step 1: Atualizar o CLAUDE.md**

Refletir o estado novo (seguindo o estilo das seções existentes):
- Seção "Estado atual do repositório": `api` ganhou a gestão de KB (`/api/v1/knowledge-base`), `worker` implementou `ingest_knowledge_base_file`, `web` ganhou `/base-de-conhecimento`, `agents` ganhou `buscar_base_conhecimento_escritorio`.
- Seção "Frontend": marcar `/base-de-conhecimento` como ✅ com o que foi implementado (upload PDF/DOCX/TXT, 20 MB/arquivo, 500 MB/tenant, status, exclusão; duplicado → 409).
- Seção "RAG Service": registrar `doc_id` externo + `.txt` + `conversation_id` reservado `"kb"`.
- Seção "Infraestrutura": volume novo `kb_uploads`.
- Pendências: remover/ajustar itens resolvidos (extensões suportadas, limite de storage, status de ingestão no front, nome duplicado) e manter os que ficaram (botão reprocessar, custo em créditos de ingestão/retrieval, limite por plano).

- [ ] **Step 2: Verificação ponta a ponta local**

```bash
docker compose up -d --build api worker api_rag web
```

1. Login no `web` (`http://localhost:3000/login`, seed `admin@demo.com`/`segredo123`).
2. Acessar `/base-de-conhecimento`, subir um PDF pequeno → badge `processando` → vira `pronto` em até ~2 min (acompanhar `docker compose logs -f worker api_rag`).
3. Subir o mesmo nome de novo → erro de duplicado exibido.
4. Excluir o arquivo → some da lista (e `docker compose logs api_rag` mostra a deleção).
5. (Opcional, exige LLM configurado) Mandar mensagem no fluxo WhatsApp simulado perguntando algo que está no documento e conferir se o agente chama `buscar_base_conhecimento_escritorio` nos logs do `agents`.

Expected: fluxo completo sem erro nos logs.

- [ ] **Step 3: Commit final**

```bash
git add CLAUDE.md
git commit -m "docs: base de conhecimento implementada (api, worker, web, agents, api_rag)"
```
