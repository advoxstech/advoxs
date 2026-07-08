# Design — Gestão da Base de Conhecimento

Data: 2026-07-08
Status: aprovado

## Objetivo

Permitir que cada escritório (tenant) suba, liste e exclua documentos da sua base de conhecimento pela página `/base-de-conhecimento`, com ingestão assíncrona no `api_rag`, e que os agentes consultem essa base nas conversas do WhatsApp.

## Decisões de produto

- **Formatos aceitos**: PDF, DOCX e TXT.
- **Nome duplicado no tenant**: upload rejeitado com `409` — o usuário exclui o antigo antes de re-subir.
- **Limites**: 20 MB por arquivo (`KB_MAX_FILE_SIZE_BYTES`) e 500 MB de storage total por tenant (`KB_MAX_TOTAL_SIZE_BYTES`), ambos configuráveis por env.
- **Escopo inclui a tool dos agentes**: sem ela o upload não teria efeito nas conversas.

## Decisões de arquitetura

### Transporte dos bytes: volume compartilhado

O `api` e o `worker` compartilham o volume Docker `kb_uploads` (montado em `/data/kb_uploads` nos dois). O `api` grava o arquivo em `{KB_UPLOAD_DIR}/{tenant_id}/{file_id}`, e o `worker` lê de lá para enviar ao `api_rag`, apagando o temporário ao final. Alternativas descartadas: bytes de staging no Postgres (blobs de 20 MB no banco) e envio síncrono no request de upload (a ingestão do `api_rag` é síncrona e lenta — 10 s a 2 min — e travaria o navegador com risco de timeout).

### Escopo da KB no `api_rag`: `conversation_id` reservado `"kb"`

Documentos da base do escritório são indexados com o `tenant_id` real e o `conversation_id` reservado `"kb"`, reaproveitando as rotas `/documents/users/*` e `/retrieval/users` sem criar rotas novas — mesmo padrão do tenant reservado `"system"`. Sem risco de colisão: `conversation_id` real é sempre número de telefone.

### Identidade dos documentos: `doc_id` externo

O insert do `api_rag` passa a aceitar `doc_id` opcional (UUID) no form; o `api` envia o próprio `id` de `knowledge_base_files`. 1 arquivo = 1 documento com o mesmo UUID nos dois serviços; a exclusão usa o mesmo id, sem coluna extra e sem depender do corpo da resposta do insert.

## Componentes

### `api_rag` (mudanças pequenas)

- Aceitar `.txt` na ingestão: decodificar bytes como UTF-8 com fallback latin-1 (sem parser novo).
- `POST /documents/users/insert` aceita `doc_id` opcional (UUID): quando presente, usado como PK de `documentos_usuario` e no payload dos pontos no Qdrant.
- Atualizar `API.md` (fonte da verdade do serviço).

### `api` — router `/api/v1/knowledge-base`

Todas as rotas autenticadas com `get_current_tenant` + `get_tenant_session` (RLS ativo).

- **`POST /files`** (multipart) — validações: extensão/mime permitidos (`400`) — `.pdf`/`application/pdf`, `.docx`/`application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `.txt`/`text/plain`; a extensão do filename é a fonte da verdade e o mime declarado só precisa ser compatível ou genérico (`application/octet-stream`) —, ≤ 20 MB por arquivo (`413`), soma do storage do tenant + arquivo ≤ 500 MB (`413`, informando quanto resta), nome inédito no tenant (`409`). Efeitos: grava o arquivo no volume, insere registro com `status=processing`, enfileira `ingest_knowledge_base_file(tenant_id, file_id)` **após o commit** (mesmo padrão do webhook do WhatsApp — o worker não pode acordar antes de a linha estar visível). Retorna `202` com o registro.
- **`GET /files`** — lista do tenant, `uploaded_at` desc, paginada (mesmo padrão de `/conversations`).
- **`DELETE /files/{id}`** — recusa com `409` enquanto `status=processing` (evita corrida com o worker). Chama o delete do `api_rag` (`DELETE /documents/users/delete?tenant_id=...&docs_ids={file_id}` — idempotente lá), remove a linha e qualquer arquivo temporário restante. `404` se o registro não existir.

Novas envs: `KB_UPLOAD_DIR`, `KB_MAX_FILE_SIZE_BYTES` (default 20 MB), `KB_MAX_TOTAL_SIZE_BYTES` (default 500 MB), `RAG_API_URL`, `RAG_API_KEY`.

### `worker` — `ingest_knowledge_base_file(ctx, tenant_id, file_id)`

1. Carrega o registro; se não existir ou `status != processing`, encerra silenciosamente (idempotência em retries do Arq).
2. Lê os bytes do volume e faz `POST /documents/users/insert` no `api_rag` — multipart com `tenant_id`, `conversation_id="kb"`, `doc_id=file_id` e o arquivo; header `Authorization: <RAG_API_KEY>` (sem prefixo `Bearer`).
3. Sucesso → `status=ready` e apaga o temporário. Erro transiente (timeout/conexão/5xx) → `arq.Retry` com backoff; esgotadas as tentativas, ou erro definitivo (4xx) → `status=error` + `error_message` legível (exibido no front).

Infra: segundo `httpx.AsyncClient` no `ctx` (base_url do RAG, timeout largo — a ingestão lá é síncrona). Novas envs: `RAG_API_URL`, `RAG_API_KEY`, `KB_UPLOAD_DIR`.

### Docker Compose

Volume nomeado `kb_uploads` montado em `/data/kb_uploads` no `api` e no `worker`; envs novas nos dois serviços.

### `web` — página `/base-de-conhecimento`

- **Proxy `/api/backend`**: adicionar `knowledge-base` à allowlist (`ALLOWED_PREFIXES`), exportar handler `DELETE` e repassar multipart sem forçar `content-type: application/json`. `client-api` (`backendFetch`) idem quando o body for `FormData`.
- **Página**: server component + `KnowledgeBasePanel` (client component), espelhando o padrão de `/conversas`: upload com validação client-side (tipo/20 MB), listagem com nome, tamanho, data e badge de status (`processing` em latão, `ready` em verde, `error` em vermelho com a mensagem), exclusão com confirmação. Polling de 5 s enquanto houver arquivo `processing`. Link na nav lateral e rota nova no matcher do `middleware`.

### `agents` — tool de consulta

Nova tool `buscar_base_conhecimento_escritorio`, bindada à secretária e aos 3 especialistas: extrai o `tenant_id` do `thread_id` composto e consulta `/retrieval/users` com `conversation_id="kb"`. Prompts atualizados para orientar o uso ("consulte a base de conhecimento do escritório quando a pergunta envolver documentos ou materiais próprios do escritório"). `API_AGENTS.md` atualizado.

## Erros e casos-limite

| Caso | Comportamento |
|---|---|
| Formato não suportado | `400` no upload, mensagem clara no front |
| Arquivo > 20 MB | `413` |
| Storage do tenant estouraria 500 MB | `413` com o quanto resta disponível |
| Nome duplicado | `409` |
| `api_rag` fora do ar na ingestão | Retry com backoff; esgotou → `status=error` visível no painel |
| Delete durante `processing` | `409` |
| Retry do Arq após sucesso parcial | Job re-checa `status` antes de reprocessar |

Limitação aceita nesta entrega: arquivo em `error` fica registrado para o usuário ver o motivo e excluir/re-subir manualmente (sem botão "reprocessar").

## Testes

- **api** (`tests/unit`): rotas com dependency overrides (upload feliz, cada validação, `409` duplicado, delete ok/`409`/`404`, listagem) — enqueue do Arq e escrita em disco mockados.
- **worker** (`tests/unit`): sucesso, retry transiente, erro definitivo, idempotência (`status != processing`).
- **api_rag** (`tests/unit`): ingestão de `.txt` e `doc_id` externo.
- **agents** (`tests/unit`): tool nova com o client de retrieval mockado.
- **web** (Vitest + msw): render da lista, estados de status, erro de upload.

## Fora de escopo desta entrega

- Botão "reprocessar" para arquivos em `error`.
- Limite de storage variável por plano.
- Custo em créditos da ingestão/retrieval (pendência já registrada no CLAUDE.md).
- Re-ingestão dos dados legados do `api_rag` (pendência já registrada no CLAUDE.md).
