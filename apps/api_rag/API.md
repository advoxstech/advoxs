# API — Documentos & Retrieval (RAG Híbrido)

Serviço FastAPI para **ingestão de documentos** e **busca semântica híbrida** (RAG).
Guarda metadados no **PostgreSQL**, arquivos no **filesystem do servidor** e vetores no
**Qdrant**. A busca combina vetor **denso** (OpenAI embeddings) com vetor **esparso**
(API externa de sparse embeddings), fundidos via **RRF**, com expansão de query por
**HyDE + extração de palavras-chave** feita por um LLM.

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
1. Recebe arquivo (`pdf` ou `docx`) via `multipart/form-data`.
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

Ingestão de um documento associado a uma conversa de usuário.

- **Content-Type:** `multipart/form-data`
- **Campos:**

| Campo             | Tipo   | Obrigatório | Observação                                          |
|-------------------|--------|-------------|-----------------------------------------------------|
| `convesation_id`  | string | sim         | ⚠️ **Nome exatamente assim, com o typo** (`convesation_id`). Chave de agrupamento/filtro. |
| `file`            | file   | sim         | Apenas `.pdf` ou `.docx`.                            |

**Exemplo:**
```bash
curl -X POST http://localhost:8000/documents/users/insert \
  -H "Authorization: $API_KEY" \
  -F "convesation_id=conv-123" \
  -F "file=@contrato.pdf"
```

**Resposta `200`:**
```json
{ "mensagem": "Documentos inseridos com sucesso" }
```

**Erros:** `400` (validação / formato não suportado) · `403` (auth) · `500` (falha interna).

---

### 3.3 `DELETE /documents/users/delete`

- **Parâmetros (query string):** `docs_ids` — repetível (lista).

**Exemplo:**
```bash
curl -X DELETE "http://localhost:8000/documents/users/delete?docs_ids=<uuid1>&docs_ids=<uuid2>" \
  -H "Authorization: $API_KEY"
```

**Resposta `200`:**
```json
{ "mensagem": "Documentos deletados com sucesso" }
```

**Erros:** `404` (não encontrado) · `403` (auth) · `500`.
> Ver §7 (ressalvas) — o fluxo de delete tem incompatibilidades conhecidas com o repositório/modelo.

---

### 3.4 `POST /documents/system/insert`

Ingestão de documento na base "do sistema" (base de conhecimento global,
com rastreio de origem no Drive).

- **Content-Type:** `multipart/form-data`
- **Campos:**

| Campo             | Tipo   | Obrigatório | Observação                                                   |
|-------------------|--------|-------------|--------------------------------------------------------------|
| `conversation_id` | string | sim         | Aqui **sem typo**. Usado como `base` (agrupamento/filtro).   |
| `id_drive`        | string | sim         | Identificador de origem (ex.: ID no Google Drive).           |
| `file`            | file   | sim         | Apenas `.pdf` ou `.docx`.                                    |

> Nota: internamente o valor de `conversation_id` é usado como o campo **`base`**
> (nome/partição da coleção do sistema) e como filtro de busca em `/retrieval/system`.

**Exemplo:**
```bash
curl -X POST http://localhost:8000/documents/system/insert \
  -H "Authorization: $API_KEY" \
  -F "conversation_id=juridico" \
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

Busca híbrida na coleção do sistema (`COLLECTION_SISTEMA`), filtrada por `base`.

- **Content-Type:** `application/json`
- **Body:**

| Campo     | Tipo   | Obrigatório | Descrição                                     |
|-----------|--------|-------------|-----------------------------------------------|
| `base`    | string | sim         | Partição a filtrar (o `conversation_id` usado na ingestão do sistema). |
| `message` | string | sim         | Pergunta / consulta em linguagem natural.     |

**Exemplo:**
```bash
curl -X POST http://localhost:8000/retrieval/system \
  -H "Authorization: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"base": "juridico", "message": "Qual o prazo para recurso?"}'
```

**Resposta `200`:**
```json
{
  "results": [
    {
      "chunk_id": "b1e2...",
      "score": 0.82,
      "text": "…",
      "metadata": { "base": "juridico", "name": "lei.pdf", "doc_id": "…", "id_drive": "…" }
    }
  ]
}
```
Cada item corresponde ao dataclass `RetrievalResult` (`chunk_id`, `score`, `text`, `metadata`).
Lista vazia (`{"results": []}`) quando a busca no Qdrant falha ou não há hits.

---

### 3.7 `POST /retrieval/users`

Busca híbrida na coleção de usuários (`COLLECTION_USERS`), filtrada por `conversation_id`.

- **Content-Type:** `application/json`
- **Body:**

| Campo             | Tipo   | Obrigatório | Descrição                              |
|-------------------|--------|-------------|----------------------------------------|
| `conversation_id` | string | sim         | Conversa a filtrar (ingestão do usuário). |
| `message`         | string | sim         | Pergunta / consulta.                   |

```bash
curl -X POST http://localhost:8000/retrieval/users \
  -H "Authorization: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"conversation_id": "conv-123", "message": "resumo do contrato"}'
```

Mesmo formato de resposta de §3.6.

---

## 4. Modelo de dados (PostgreSQL)

Tabelas criadas automaticamente no *startup* (`Base.metadata.create_all`) e versionadas
com Alembic (`alembic/versions/`).

### `documentos_usuario`
| Coluna            | Tipo      | Notas                                   |
|-------------------|-----------|-----------------------------------------|
| `id`              | UUID (PK) | default `uuid4`                         |
| `conversation_id` | String    | chave de agrupamento                    |
| `nome`            | String    | nome do arquivo, obrigatório            |
| `extensao`        | String    | `pdf` / `docx`, obrigatório             |
| `path_base`       | String    | raiz de armazenamento (`UPLOAD_DIR_USER`) |
| `path_doc`        | String    | subpasta (= `conversation_id`)          |
| `criado_em`       | DateTime  | default `utcnow`                        |

### `documentos_sistema`
| Coluna       | Tipo      | Notas                                     |
|--------------|-----------|-------------------------------------------|
| `id`         | UUID (PK) | default `uuid4`                           |
| `id_drive`   | String    | origem (ex.: Drive)                       |
| `base`       | String    | partição (= `conversation_id` da ingestão) |
| `nome`       | String    | nome do arquivo                           |
| `extensao`   | String    | `pdf` / `docx`                            |
| `path_base`  | String    | raiz (`UPLOAD_DIR_SYSTEM`)                |
| `path_doc`   | String    | subpasta (= `base`)                       |
| `criado_em`  | DateTime  | default `utcnow`                          |

Arquivo cru gravado em: `{path_base}/{path_doc}/{nome}`.

---

## 5. Qdrant (vetores)

Duas coleções, nomeadas pelas envs `COLLECTION_SISTEMA` e `COLLECTION_USERS`.
Cada ponto usa **vetores nomeados**:

- `dense` — embedding OpenAI (`text-embedding-3-small`, 1536 dims), distância padrão.
- `sparse` — vetor esparso (`indices` + `values`) vindo da API local de sparse.

**Payload dos pontos:**

- Usuário: `{ conversation_id, name, doc_id, texto }`
- Sistema: `{ base, name, doc_id, id_drive, texto }`

Busca: dois `Prefetch` (denso e esparso), cada um com `limit=PREFETCH_K` e o mesmo
`payload_filter`, fundidos por `FusionQuery(Fusion.RRF)`, retornando `TOP_K` resultados.

> ⚠️ **As coleções precisam existir com esses vetores nomeados antes do uso.** O cliente
> (`clients/qdrant.py`) faz `upsert`/`search`/`delete`, mas **não há criação de coleção
> funcional exposta** (o `test_connection` referencia `create_collection`/`delete_collection`
> que não estão implementados). Crie as coleções manualmente com os vetores `dense` e
> `sparse` no provisionamento do ambiente.

---

## 6. Configuração (variáveis de ambiente)

Carregadas de `.env` via `python-dotenv`. **Não versione segredos reais.**

| Variável               | Descrição                                          | Exemplo                          |
|------------------------|----------------------------------------------------|----------------------------------|
| `API_KEY`              | Chave da API (header `Authorization`). Obrigatória.| `troque-me`                      |
| `OPENAI_API_KEY`       | Chave OpenAI (embeddings denso + chat HyDE).       | `sk-...`                         |
| `QDRANT_URL`           | URL do Qdrant.                                     | `http://localhost:6333`          |
| `QDRANT_API_KEY`       | API key do Qdrant (opcional).                      | —                                |
| `COLLECTION_SISTEMA`   | Nome da coleção do sistema.                        | `documentos_sistema`             |
| `COLLECTION_USERS`     | Nome da coleção de usuários.                       | `documentos_usuarios`            |
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
| `POSTGRES_DB`          | Banco.                                             | `root_db`                        |
| `SECRET_KEY`           | Reservado (não usado nas rotas atuais).            | —                                |
| `CHATVOLT_API_KEY`     | Reservado (não usado no código atual).             | —                                |

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
> As tabelas também são criadas no startup via `create_all`; para produção prefira Alembic.

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

Pontos do estado atual do código que o projeto integrador deve conhecer:

1. **Typo no form de usuário:** `POST /documents/users/insert` espera o campo
   `convesation_id` (sem o segundo "r"). O cliente precisa enviar exatamente assim,
   senão retorna `422`.

2. **Campo de texto no retorno do retrieval:** o retrieval lê `payload.get("text")`,
   mas a ingestão grava o texto do chunk sob a chave **`texto`**. Assim, `text` tende a
   vir **vazio** no `RetrievalResult`; o conteúdo real fica em `metadata.texto`.
   Alinhe as chaves (`text` vs `texto`) ao integrar.

3. **Fluxo de delete inconsistente:** o service chama métodos que não existem no
   repositório (`buscar_documento_por_id`, `deletar_documento`) e acessa `doc.doc_id`,
   que não é coluna dos modelos (a coluna é `id`; `doc_id` só existe no payload do Qdrant).
   Consequência: os endpoints de delete provavelmente **falham (500)** no estado atual.

4. **Filtro de delete no Qdrant:** `delete_points_by_filter` define o parâmetro `field`,
   mas o service chama com `fild=` (typo) — chamada quebra por argumento inválido.

5. **Criação de coleções do Qdrant:** não é feita pela API (ver §5). Provisione as
   coleções (`dense` + `sparse`) antes de usar.

6. **Sparse embedding é síncrono:** usa `requests` dentro de código async (bloqueante).
   Sob carga, considere migrar para cliente async ao integrar.

7. **Segredos no `.env`:** o `.env` do repositório contém credenciais reais
   (OpenAI, Postgres, Qdrant). **Rotacione essas chaves** e não as reutilize no projeto
   integrado.

---

## 10. Estrutura do projeto

```
api/
├── main.py                        # app FastAPI + lifespan (cria dirs e tabelas)
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
├── clients/qdrant.py              # QdrantClient async (upsert/search/delete)
├── database/
│   ├── models.py                  # DocumentoUsuario, DocumentoSistema
│   ├── session.py                 # engine async + get_session
│   └── repositories/documento.py  # DocumentoRepository
├── alembic/                       # migrations
├── docker-compose.yml             # api + postgres + qdrant
├── Dockerfile                     # python:3.13-slim + uv
└── pyproject.toml                 # dependências (uv)
```
