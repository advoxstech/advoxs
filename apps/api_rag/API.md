# API — Documentos & Retrieval (RAG Híbrido)

Serviço FastAPI para **ingestão de documentos** e **busca semântica híbrida** (RAG).
Guarda metadados no **PostgreSQL**, arquivos no **filesystem do servidor** e vetores no
**Qdrant**. A busca combina vetor **denso** (OpenAI embeddings) com vetor **esparso**
(API externa de sparse embeddings), fundidos via **RRF**, com expansão de query por
**HyDE + extração de palavras-chave** feita por um LLM.

**Multi-tenancy:** collection **única** no Qdrant com `tenant_id` como payload
indexado. Todo acesso (busca, deleção, upsert) passa **obrigatoriamente** por filtro
de `tenant_id` na camada de acesso (`clients/qdrant.py`) — nunca é opcional nem
decisão do chamador de alto nível. A base de conhecimento da plataforma
(compartilhada entre escritórios) usa o tenant reservado **`system`**
(`constants.SYSTEM_TENANT_ID`), que nunca deve ser usado por um escritório real.

Este documento serve como referência para integrar esta API a outro projeto.

---

## 1. Visão geral da arquitetura

```
                         ┌─────────────────────────────┐
   Cliente  ── HTTP ──▶  │        FastAPI (main)       │
   (Authorization: KEY)  │  /documents/*  /retrieval/* │
                         └──────────────┬──────────────┘
                                        │
              ┌─────────────────────────┼──────────────────────────┐
              ▼                         ▼                          ▼
      ┌───────────────┐        ┌────────────────┐         ┌────────────────┐
      │  PostgreSQL   │        │  Filesystem    │         │     Qdrant     │
      │  (metadados)  │        │  (arquivo cru) │         │  (vetores RAG) │
      └───────────────┘        └────────────────┘         └────────────────┘

   Dependências externas de embedding:
     • OpenAI Embeddings  (vetor denso)   — text-embedding-3-small
     • OpenAI Chat        (HyDE/keywords) — gpt-5-mini
     • API local Sparse   (vetor esparso) — URL_API_LOCAL_SPARSE  (POST /embed)
```

**Stack:** Python 3.13 · FastAPI · SQLAlchemy (async / asyncpg) · Alembic ·
Qdrant (`qdrant-client` async) · LangChain + `langchain-openai` · `chonkie`
(chunking) · `pdfplumber` / `python-docx` (extração de texto) · `loguru`.

### Fluxo de ingestão
1. Recebe arquivo (`pdf`, `docx` ou `txt`) via `multipart/form-data`.
2. Extrai texto → divide em *chunks* (`RecursiveChunker`).
3. Gera embeddings **denso** (OpenAI) e **esparso** (API externa) por chunk.
4. Salva o arquivo cru no disco (`UPLOAD_DIR_*`).
5. Grava metadados no Postgres.
6. Faz *upsert* dos pontos no Qdrant com payload de filtro.

### Fluxo de retrieval
1. Recebe a query.
2. LLM transforma a query em `hyde` (parágrafo hipotético → denso) + `keywords`
   (→ esparso).
3. Gera os dois vetores.
4. `query_points` no Qdrant com dois *prefetch* (denso + esparso) fundidos por **RRF**.
5. Retorna os chunks ordenados por score.

---

## 2. Autenticação

Todas as rotas (exceto `/health`) exigem uma **API Key** enviada no header
`Authorization` — **valor cru, sem prefixo `Bearer`**.

```
Authorization: <API_KEY>
```

Definida na variável de ambiente `API_KEY`. A validação usa
`secrets.compare_digest` (`api/security.py`). Falha → **403 Forbidden**
(`{"detail": "API Key inválida ou ausente"}`).

> ⚠️ O header **não** é `X-API-Key` nem `Bearer`. É literalmente `Authorization: <chave>`.

---

## 3. Endpoints

Base URL padrão: `http://<host>:8000`

| Método | Rota                        | Auth | Corpo               | Descrição                              |
|--------|-----------------------------|------|---------------------|----------------------------------------|
| GET    | `/health`                   | não  | —                   | Health check                           |
| POST   | `/documents/users/insert`   | sim  | `multipart/form-data` | Ingestão de documento do **usuário**   |
| DELETE | `/documents/users/delete`   | sim  | query string        | Deleta documento(s) do **usuário**     |
| POST   | `/documents/system/insert`  | sim  | `multipart/form-data` | Ingestão de documento do **sistema**   |
| DELETE | `/documents/system/delete`  | sim  | query string        | Deleta documento(s) do **sistema**     |
| POST   | `/retrieval/system`         | sim  | `application/json`   | Busca híbrida na base do **sistema**   |
| POST   | `/retrieval/users`          | sim  | `application/json`   | Busca híbrida na base do **usuário**   |

Documentação interativa (Swagger) disponível em `/docs`; OpenAPI em `/openapi.json`.

---

### 3.1 `GET /health`

Sem autenticação.

**Resposta `200`:**
```json
{ "status": "ok", "timestamp": "2026-07-07T12:00:00.000000" }
```

---

### 3.2 `POST /documents/users/insert`

Ingestão de um documento associado a uma conversa de usuário, escopado por tenant.

- **Content-Type:** `multipart/form-data`
- **Campos:**

| Campo             | Tipo   | Obrigatório | Observação                                          |
|-------------------|--------|-------------|-----------------------------------------------------|
| `tenant_id`       | string | sim         | Escritório dono do documento.                        |
| `conversation_id` | string | sim         | Conversa/contato. Valor reservado: `"kb"` (base de conhecimento do escritório, gerida pelo `api`/`worker`). |
| `doc_id`          | string | não         | UUID do documento (chave externa para re-ingestão idempotente). Quando presente: se já existir documento com esse id, é deletado antes (substituição completa — disco + Qdrant + Postgres). Ausente: gera um novo UUID. |
| `file`            | file   | sim         | Apenas `.pdf`, `.docx` ou `.txt`.                    |

**Exemplo:**
```bash
curl -X POST http://localhost:8000/documents/users/insert \
  -H "Authorization: $API_KEY" \
  -F "tenant_id=<uuid-do-tenant>" \
  -F "conversation_id=5511999998888" \
  -F "file=@contrato.pdf"
```

**Com `doc_id` (re-ingestão idempotente):**
```bash
curl -X POST http://localhost:8000/documents/users/insert \
  -H "Authorization: $API_KEY" \
  -F "tenant_id=<uuid-do-tenant>" \
  -F "conversation_id=kb" \
  -F "doc_id=<uuid-do-doc>" \
  -F "file=@regimento.txt"
```

**Resposta `200`:**
```json
{ "mensagem": "Documentos inseridos com sucesso" }
```

**Erros:** `400` (validação / formato não suportado) · `403` (auth) · `500` (falha interna).

---

### 3.3 `DELETE /documents/users/delete`

- **Parâmetros (query string):** `tenant_id` (obrigatório) e `docs_ids` — repetível (lista).

Documento que não pertence ao `tenant_id` informado é **ignorado** (não deleta nem
vaza a existência) — a resposta continua `200`.

**Exemplo:**
```bash
curl -X DELETE "http://localhost:8000/documents/users/delete?tenant_id=<uuid>&docs_ids=<uuid1>&docs_ids=<uuid2>" \
  -H "Authorization: $API_KEY"
```

**Resposta `200`:**
```json
{ "mensagem": "Documentos deletados com sucesso" }
```

**Erros:** `400` (tenant_id ausente/inválido) · `403` (auth) · `500`.

---

### 3.4 `POST /documents/system/insert`

Ingestão de documento na base da plataforma (compartilhada entre todos os
escritórios, com rastreio de origem no Drive). Indexado no Qdrant sob o tenant
reservado `system`.

- **Content-Type:** `multipart/form-data`
- **Campos:**

| Campo      | Tipo   | Obrigatório | Observação                                                   |
|------------|--------|-------------|--------------------------------------------------------------|
| `base`     | string | sim         | Categoria/partição (ex.: `condominial`, `contratos`) — o mesmo valor filtrado em `/retrieval/system`. |
| `id_drive` | string | sim         | Identificador de origem (ex.: ID no Google Drive).           |
| `file`     | file   | sim         | Apenas `.pdf` ou `.docx`.                                    |

**Exemplo:**
```bash
curl -X POST http://localhost:8000/documents/system/insert \
  -H "Authorization: $API_KEY" \
  -F "base=condominial" \
  -F "id_drive=1AbC..." \
  -F "file=@lei.pdf"
```

**Resposta `200`:** `{ "mensagem": "Documentos inseridos com sucesso" }`

---

### 3.5 `DELETE /documents/system/delete`

Igual a §3.3, porém na base do sistema. Query: `docs_ids` (repetível).

```bash
curl -X DELETE "http://localhost:8000/documents/system/delete?docs_ids=<uuid1>" \
  -H "Authorization: $API_KEY"
```

---

### 3.6 `POST /retrieval/system`

Busca híbrida na base da plataforma — internamente filtrada por
`tenant_id = "system"` + `base`.

- **Content-Type:** `application/json`
- **Body:**

| Campo     | Tipo   | Obrigatório | Descrição                                     |
|-----------|--------|-------------|-----------------------------------------------|
| `base`    | string | sim         | Categoria a filtrar (a `base` usada na ingestão do sistema). |
| `message` | string | sim         | Pergunta / consulta em linguagem natural.     |

**Exemplo:**
```bash
curl -X POST http://localhost:8000/retrieval/system \
  -H "Authorization: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"base": "condominial", "message": "Qual o prazo para recurso?"}'
```

**Resposta `200`:**
```json
{
  "results": [
    {
      "chunk_id": "b1e2...",
      "score": 0.82,
      "text": "…",
      "metadata": { "tenant_id": "system", "base": "condominial", "name": "lei.pdf", "doc_id": "…", "id_drive": "…" }
    }
  ]
}
```
Cada item corresponde ao dataclass `RetrievalResult` (`chunk_id`, `score`, `text`, `metadata`).
Lista vazia (`{"results": []}`) quando a busca no Qdrant falha ou não há hits.

---

### 3.7 `POST /retrieval/users`

Busca híbrida nos documentos do contato, filtrada por `tenant_id` + `conversation_id`.

- **Content-Type:** `application/json`
- **Body:**

| Campo             | Tipo   | Obrigatório | Descrição                              |
|-------------------|--------|-------------|----------------------------------------|
| `tenant_id`       | string | sim         | Escritório dono dos documentos.        |
| `conversation_id` | string | sim         | Conversa a filtrar (ingestão do usuário). |
| `message`         | string | sim         | Pergunta / consulta.                   |

```bash
curl -X POST http://localhost:8000/retrieval/users \
  -H "Authorization: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "<uuid>", "conversation_id": "5511999998888", "message": "resumo do contrato"}'
```

> No `agents`, o client (`clients/retrieval.py`) divide o `thread_id` composto
> `"{tenant_id}:{contact_phone_number}"` e envia os dois campos separados.

Mesmo formato de resposta de §3.6.

---

## 4. Modelo de dados (PostgreSQL)

Tabelas criadas automaticamente no *startup* (`Base.metadata.create_all`) e versionadas
com Alembic (`alembic/versions/`).

### `documentos_usuario`
| Coluna            | Tipo      | Notas                                   |
|-------------------|-----------|-----------------------------------------|
| `id`              | UUID (PK) | default `uuid4` (ou `doc_id` externo quando re-ingestão idempotente) |
| `tenant_id`       | String    | escritório dono — indexado; obrigatório na aplicação (nullable no banco só por causa de linhas legadas, ver migration `a1b2c3d4e5f6`) |
| `conversation_id` | String    | chave de agrupamento                    |
| `nome`            | String    | nome do arquivo, obrigatório            |
| `extensao`        | String    | `pdf` / `docx` / `txt`, obrigatório    |
| `path_base`       | String    | raiz de armazenamento (`UPLOAD_DIR_USER`) |
| `path_doc`        | String    | subpasta (= `{tenant_id}/{conversation_id}`) |
| `criado_em`       | DateTime  | default `utcnow`                        |

### `documentos_sistema`
| Coluna       | Tipo      | Notas                                     |
|--------------|-----------|-------------------------------------------|
| `id`         | UUID (PK) | default `uuid4`                           |
| `id_drive`   | String    | origem (ex.: Drive)                       |
| `base`       | String    | partição (= `conversation_id` da ingestão) |
| `nome`       | String    | nome do arquivo                           |
| `extensao`   | String    | `pdf` / `docx` / `txt`                    |
| `path_base`  | String    | raiz (`UPLOAD_DIR_SYSTEM`)                |
| `path_doc`   | String    | subpasta (= `base`)                       |
| `criado_em`  | DateTime  | default `utcnow`                          |

Arquivo cru gravado em: `{path_base}/{path_doc}/{nome}`.

---

## 5. Qdrant (vetores)

**Collection única**, nomeada pela env `QDRANT_COLLECTION` (default `advoxs_kb`),
provisionada **automaticamente no startup** (`ensure_collection`, com retry para
esperar o Qdrant subir no compose). Cada ponto usa **vetores nomeados**:

- `dense` — embedding OpenAI (`text-embedding-3-small`, `DENSE_VECTOR_SIZE`=1536 dims), distância cosseno.
- `sparse` — vetor esparso (`indices` + `values`) vindo da API local de sparse.

**Payload dos pontos** (o texto do chunk vai na chave `text` — a mesma lida pelo retrieval):

- Usuário: `{ tenant_id, conversation_id, name, doc_id, text }`
- Sistema: `{ tenant_id: "system", base, name, doc_id, id_drive, text }`

Índices de payload (keyword) criados no startup: `tenant_id`, `base`,
`conversation_id`, `doc_id`.

**Isolamento:** `clients/qdrant.py` exige `tenant_id` em toda busca/deleção
(`ValueError` sem ele) e rejeita upsert de ponto sem `tenant_id` no payload.
O filtro é aplicado nos dois ramos (`Prefetch` denso e esparso), cada um com
`limit=PREFETCH_K`, fundidos por `FusionQuery(Fusion.RRF)`, retornando `TOP_K`
resultados.

---

## 6. Configuração (variáveis de ambiente)

Carregadas de `.env` via `python-dotenv`. **Não versione segredos reais.**

| Variável               | Descrição                                          | Exemplo                          |
|------------------------|----------------------------------------------------|----------------------------------|
| `API_KEY`              | Chave da API (header `Authorization`). Obrigatória.| `troque-me`                      |
| `OPENAI_API_KEY`       | Chave OpenAI (embeddings denso + chat HyDE).       | `sk-...`                         |
| `QDRANT_URL`           | URL do Qdrant.                                     | `http://localhost:6333`          |
| `QDRANT_API_KEY`       | API key do Qdrant (opcional).                      | —                                |
| `QDRANT_COLLECTION`    | Nome da collection única.                          | `advoxs_kb`                      |
| `DENSE_VECTOR_SIZE`    | Dimensão do vetor denso.                           | `1536`                           |
| `DENSE_MODEL`          | Modelo de embedding denso OpenAI.                  | `text-embedding-3-small`         |
| `CHAT_MODEL`           | Modelo de chat para HyDE/keywords.                 | `gpt-5-mini`                     |
| `URL_API_LOCAL_SPARSE` | Endpoint da API de sparse embeddings (`POST`).     | `http://host:8001/embed`         |
| `TOP_K`                | Nº de resultados finais.                           | `5`                              |
| `PREFETCH_K`           | Candidatos por ramo antes do RRF.                  | `20`                             |
| `UPLOAD_DIR_USER`      | Diretório dos arquivos de usuário.                 | `/var/documentos_user`           |
| `UPLOAD_DIR_SYSTEM`    | Diretório dos arquivos do sistema.                 | `/var/documentos_system`         |
| `POSTGRES_USER`        | Usuário Postgres.                                  | `postgres`                       |
| `POSTGRES_PASSWORD`    | Senha Postgres.                                    | —                                |
| `POSTGRES_HOST`        | Host Postgres.                                     | `localhost`                      |
| `POSTGRES_PORT`        | Porta Postgres.                                    | `5432`                           |
| `POSTGRES_DB`          | Banco.                                             | `advoxs_rag`                     |

Ver `.env.example` na raiz do serviço.

### API externa de Sparse Embeddings

O serviço depende de uma API HTTP separada em `URL_API_LOCAL_SPARSE`:

- **Request:** `POST` com JSON `{ "document_id": "<str>", "texts": ["...", "..."] }`
- **Response esperada:** `{ "vectors": [ { "indices": [int...], "values": [float...] }, ... ] }`

Um item por texto, na mesma ordem. Essa API **não faz parte deste repositório** e precisa
estar disponível para ingestão e retrieval funcionarem.

---

## 7. Como rodar

### Local (uv)
```bash
uv sync
# preencher .env
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# ou:  uv run python main.py
```

### Docker Compose
Sobe `api` + `postgres` (17-alpine) + `qdrant`:
```bash
docker compose up --build
```
No compose, `POSTGRES_HOST=postgres` e `POSTGRES_PORT=5432` sobrescrevem o `.env`.
A API não sobe até o Postgres passar no healthcheck. Volumes persistem Postgres,
Qdrant e os diretórios de upload.

### Migrations (Alembic)
```bash
uv run alembic upgrade head
```
> As tabelas também são criadas no startup via `create_all` (conveniência para
> ambiente novo); para produção prefira Alembic — rode as migrations **antes**
> de subir a API.

### Testes e lint
```bash
uv run pytest tests/unit
uv run ruff check .
```

---

## 8. Padrões de resposta e erros

- Sucesso de escrita/delete: `{ "mensagem": "..." }`
- Retrieval: `{ "results": [ {chunk_id, score, text, metadata}, ... ] }`
- Erros seguem `HTTPException` do FastAPI: `{ "detail": "<mensagem>" }`

| Código | Quando                                                     |
|--------|------------------------------------------------------------|
| 400    | Validação / formato de arquivo não suportado               |
| 403    | API Key inválida ou ausente                                |
| 404    | Documento não encontrado (delete / retrieval)              |
| 422    | Corpo/parâmetros mal formados (validação do FastAPI)       |
| 500    | Erro interno (Qdrant, OpenAI, filesystem, banco, etc.)     |

---

## 9. Ressalvas conhecidas (importante para a integração)

Bugs históricos **já corrigidos** no retrofit multi-tenant (2026-07): typo
`convesation_id` no form de usuário; mismatch `text`/`texto` entre ingestão e
retrieval; fluxo de delete chamando métodos inexistentes no repositório
(`buscar_documento_por_id`/`deletar_documento`/`doc.doc_id`); typo `fild=` no
filtro de delete do Qdrant; coleções não provisionadas pela API; sparse
embedding síncrono (`requests`) em código async; `TOP_K`/`PREFETCH_K` lidos
como string.

Pontos que **permanecem** relevantes:

1. **Dados legados sem tenant:** pontos indexados antes do retrofit (nas
   coleções antigas `COLLECTION_SISTEMA`/`COLLECTION_USERS`) não têm
   `tenant_id` e ficam **invisíveis** para a collection única nova — precisam
   ser re-ingeridos. O mesmo vale para linhas antigas de `documentos_usuario`
   (coluna `tenant_id` nullable por isso).

2. **Auth por API key única global:** o serviço continua com uma chave só
   (`API_KEY`), adequada apenas como **serviço interno** (chamado por
   `agents`/`api`/`worker`) — nunca exposto direto ao escritório. O isolamento
   por tenant depende de o chamador enviar o `tenant_id` correto.

3. **Custo em créditos não instrumentado:** ingestão e retrieval não geram
   `credit_transactions`.

4. **Segredos no `.env`:** o `.env` local contém credenciais reais
   (OpenAI, Postgres, Qdrant). **Rotacione essas chaves** e não as reutilize no
   projeto integrado.

---

## 10. Estrutura do projeto

```
api_rag/
├── main.py                        # app FastAPI + lifespan (dirs, tabelas, collection Qdrant)
├── constants.py                   # SYSTEM_TENANT_ID, QDRANT_COLLECTION, DENSE_VECTOR_SIZE
├── api/
│   ├── security.py                # verify_api_key (header Authorization)
│   └── routes/
│       ├── health.py              # GET /health
│       ├── retrievals.py          # POST /retrieval/system | /retrieval/users
│       └── documents/
│           ├── users.py           # /documents/users/insert | /delete
│           └── system.py          # /documents/system/insert | /delete
├── services/
│   ├── documents/main.py          # DocumentoService (ingestão/delete)
│   └── retrieval/main.py          # RetrievalService (HyDE + híbrido)
├── clients/qdrant.py              # QdrantClient async (ensure_collection/upsert/search/delete, filtro de tenant obrigatório)
├── database/
│   ├── models.py                  # DocumentoUsuario (tenant_id), DocumentoSistema
│   ├── session.py                 # engine async + get_session
│   └── repositories/documento.py  # DocumentoRepository
├── alembic/                       # migrations
├── tests/unit/                    # pytest (isolamento por tenant, contratos das rotas)
├── docker-compose.yml             # api + postgres + qdrant
├── Dockerfile                     # python:3.13-slim + uv
└── pyproject.toml                 # dependências (uv)
```
