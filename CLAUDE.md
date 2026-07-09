# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Guia de contexto e convenções do projeto para o Claude Code e demais colaboradores.

## Estado atual do repositório

Este repositório está em fase de transição entre planejamento e implementação:

- `apps/api` já implementa o **fluxo de mensagem entrante do WhatsApp**, a **autenticação JWT** e a **gestão da base de conhecimento**: modelo de dados completo (migrations Alembic `0001`+`0002`, todas as tabelas da seção "Modelo de Dados" + RLS), webhook da Meta (`GET`/`POST /api/v1/webhooks/whatsapp`, com validação de `X-Hub-Signature-256` quando `META_APP_SECRET` setado), resolução de tenant via `phone_number_id`, persistência em `conversations`/`messages` (dedup por `wa_message_id`), enfileiramento no Arq, auth completa (`/api/v1/auth/{login,refresh,logout}`, ver seção Autenticação), `/api/v1/knowledge-base/files` (upload/listagem/exclusão, ver seção Frontend), `/api/v1/whatsapp/{connect,connection,disconnect}` (conexão manual do número, ver seção Integração WhatsApp Business) e o **cadastro self-service com pagamento** — `/api/v1/credit-packages` (listagem pública), `/api/v1/signup/{checkout,status}` e `/api/v1/webhooks/stripe` (ver seção Billing / Créditos). Há um seed de dev (`scripts/seed_dev.py`) que cria tenant + usuário + número WhatsApp cifrado para exercitar o fluxo ponta a ponta (o cadastro self-service é a via preferida agora pra criar um tenant, mas o seed ainda serve pra debug local). Ainda **não** tem: dashboard `/rom`, `/admin`. Comandos: `uv run pytest tests/unit`, `uv run ruff check .`, `uv run alembic upgrade head` (dentro de `apps/api`).
- `apps/worker` implementa `process_inbound_message`: checa o estado da conversa (`agent`|`human`), descriptografa o access token do tenant (Fernet, env `WHATSAPP_TOKEN_ENCRYPTION_KEY`), chama o `agents` via `POST /messages` (retry com backoff em erro transiente; 202 = debounce agrupou) e persiste as respostas do agente em `messages`. `ingest_knowledge_base_file` lê o arquivo do volume compartilhado `kb_uploads`, envia ao `api_rag` (`doc_id` = id do registro, `conversation_id="kb"`) e atualiza `status` (`ready`/`error`, com retry com backoff em erro transiente). Mesmos comandos de teste/lint do `api`.
- `apps/web` implementa **login, o painel de conversas e a gestão da base de conhecimento**: `/login` (server action → cookies httpOnly com os tokens do `api`), middleware de proteção de rotas, proxy autenticado (`/api/backend/*` → `api`, com suporte a multipart/DELETE e refresh transparente do access token no 401), `/conversas` (lista com polling, thread, toggle de takeover e resposta manual) e `/base-de-conhecimento` (upload PDF/DOCX/TXT até 20 MB/arquivo e 500 MB/tenant, listagem com status `processando`/`pronto`/`erro` via polling condicional, exclusão com confirmação; nome duplicado → erro 409 exibido). Design tokens em `globals.css`/`tailwind.config.ts` (papel frio + verde-tinta + latão para o estado manual; fontes Spectral/IBM Plex via `next/font`). Comandos: `pnpm test`, `pnpm lint`, `pnpm build` (dentro de `apps/web`). Ainda não tem: `/rom` (dashboard), billing, `/admin`.
- `apps/agents` e `apps/api_rag` **já existem como código real**: são dois projetos standalone, construídos anteriormente para um único escritório/cliente (fora deste monorepo), agora trazidos para cá para se tornarem o coração da plataforma (execução de agentes e RAG, respectivamente). Ambos são FastAPI + Python 3.13, gerenciados por `uv`, com `Dockerfile`/`docker-compose.yml` próprios.
- **Ambos foram construídos single-tenant** (sem noção de `tenant_id`) — ver seções "Agents Service" e "RAG Service" abaixo para o detalhamento de features e o que precisa ser adaptado para multi-tenancy antes de irem para produção nesta plataforma.
- Os `README.md` desses dois projetos estão vazios; a documentação real está em `apps/agents/API_AGENTS.md` e `apps/api_rag/API.md` — são a fonte da verdade sobre o comportamento atual de cada serviço e devem ser consultados (e mantidos atualizados) sempre que o código deles mudar.
- ⚠️ Os `.env` copiados junto com esses projetos contêm credenciais reais (OpenAI, Postgres, Qdrant, Chatwoot). **Rotacionar todas antes de reutilizar** e garantir que `.env` seguem ignorados pelo git (nunca commitar segredo real).
- Quando `web`, `api` e `worker` forem implementados, e quando `agents`/`api_rag` forem adaptados para multi-tenancy, esta seção deve ser atualizada com comandos reais de build/lint/test de cada app.

## Visão do produto

Plataforma **multi-tenant B2B** que fornece **agentes de IA prontos** para escritórios de advocacia.

- Cada tenant é um **escritório de advocacia**.
- Os **agentes são fixos e bem definidos pela plataforma** (não são criados/customizados pelo usuário).
- O que cada escritório pode personalizar:
  - Adicionar suas próprias **bases de conhecimento** (RAG).
  - Conectar um **número de WhatsApp Business** para que os agentes atendam clientes/contatos do escritório por lá.
- Os agentes usam **tools** (ex: geração de documentos, consulta a base de conhecimento) para executar tarefas.

## Arquitetura geral

Monorepo com múltiplos apps. O serviço de agentes é **isolado como microserviço** por receber requests de todos os tenants simultaneamente e concentrar a orquestração LangGraph. O RAG (ingestão + retrieval) também é isolado em microserviço próprio (`api_rag`), separado do backend geral (`api`), porque tem dependências pesadas específicas (Qdrant, embeddings, chunking, parsing de PDF/DOCX) e é consumido tanto pelo `agents` (tools de conhecimento) quanto, futuramente, pelo `web`/`worker` (upload/gestão de KB).

```
apps/
  web/          # Next.js — painel do escritório (auth, gestão de KB, config WhatsApp)               [scaffold]
  api/          # FastAPI — backend geral: tenants, usuários, billing, integrações, orquestra webhooks [webhook WhatsApp + modelo de dados prontos; resto a implementar]
  agents/       # FastAPI — microserviço dedicado, executa os agentes (LangGraph)                     [código existente — ver "Agents Service"]
  api_rag/      # FastAPI — microserviço dedicado de RAG: ingestão de documentos + retrieval híbrido  [código existente — ver "RAG Service"]
  worker/       # Arq — jobs assíncronos (ingestão de KB, processamento de mensagens)                  [processamento de mensagens pronto; ingestão de KB a implementar]
packages/
  ui/           # componentes compartilhados (shadcn/ui)
  types/        # tipos TS compartilhados (contratos de API)
  config/       # eslint, tsconfig, configs compartilhadas
infra/
  postgres/
  qdrant/
  redis/
docker-compose.yml
docker-compose.override.yml   # dev local
```

### Fluxo resumido (alvo — ver pendências de adaptação nas seções "Agents Service" e "RAG Service")
1. Escritório interage via painel (`web`) ou via WhatsApp Business (webhook → `api`).
2. `api` identifica o `tenant_id`, valida permissões e repassa a requisição para o `agents` service.
3. `agents` resolve qual agente (grafo LangGraph) deve ser executado, injeta o contexto do tenant (qual KB consultar via `api_rag`) e executa as tools necessárias.
4. Tools chamam `api_rag` (que acessa Qdrant), geram documentos, etc. — sempre escopadas por `tenant_id`.
5. Resposta volta pela cadeia até o canal de origem (painel ou WhatsApp).

> **Nota de arquitetura real vs. alvo:** o Chatwoot foi **removido** do `agents` — o serviço agora expõe `POST /messages` (contrato interno com `tenant_id` + credenciais do tenant) e envia respostas direto pela Graph API da Meta. O caminho webhook Meta → `api` (resolve tenant, persiste, enfileira) → `worker` (checa `agent`/`human`, chama `agents`) → respostas persistidas **já está implementado**. O onboarding do número (`/configuracoes/whatsapp` → `POST /api/v1/whatsapp/connect`) já está implementado — ver "Integração WhatsApp Business". Ver detalhes nas seções específicas de cada serviço.

## Stack e versões

| Camada | Escolha |
|---|---|
| Frontend | Next.js 15 (App Router, RSC) |
| Backend geral | FastAPI + Python 3.12 |
| Microserviço de agentes (`agents`) | FastAPI + Python 3.13 |
| Microserviço de RAG (`api_rag`) | FastAPI + Python 3.13 |
| Orquestração de agentes | LangGraph (`StateGraph` + `Command`, checkpoint em Postgres) |
| LLM (agentes) | OpenAI `gpt-5-mini` via `langchain-openai` |
| Observabilidade dos agentes | Langfuse (tracing) + Loguru |
| Banco relacional | PostgreSQL 16 (17 nos dois microserviços existentes) |
| Banco vetorial | Qdrant — busca híbrida (denso OpenAI + esparso via API própria), fusão RRF, expansão de query via HyDE |
| Cache / fila | Redis 7 (também usado hoje no `agents` para debounce de rajada de mensagens) |
| Fila de jobs assíncronos | Arq |
| Gerenciador pacotes JS | pnpm + Turborepo |
| Gerenciador pacotes Python | uv |
| Autenticação | JWT customizado no FastAPI |
| Integração de canal | WhatsApp Business (Cloud API), conexão manual do número pelo painel — Chatwoot já removido do `agents`, ver "Agents Service" |
| Infra local/deploy | Docker Compose + volumes |

## Multi-tenancy

- Isolamento por **`tenant_id`** em todas as camadas.
- **Postgres**: toda tabela multi-tenant tem coluna `tenant_id` (FK indexada, `NOT NULL`). **RLS (Row-Level Security) ativado como camada extra de proteção**, além do filtro na aplicação — cada policy filtra por `tenant_id = current_setting('app.tenant_id')::uuid`; a aplicação seta essa variável de sessão a cada request. Justificativa: dado jurídico sensível, defesa em profundidade (mesmo um bug/query sem filtro não expõe dado de outro tenant).
- **Qdrant**: **collection única** com `tenant_id` como payload indexado. Todo acesso ao Qdrant passa obrigatoriamente por filtro de `tenant_id` na camada de acesso (nunca opcional/decisão do agente).
  - ✅ **Implementado no `api_rag`**: collection única (`QDRANT_COLLECTION`, provisionada no startup), filtro de `tenant_id` obrigatório em busca/deleção e validado no upsert (`clients/qdrant.py`). A base de conhecimento da plataforma (compartilhada) usa o tenant reservado `"system"`. ⚠️ Dados indexados antes do retrofit (collections antigas) precisam ser re-ingeridos.
- **Agents service**: recebe `tenant_id` no contexto de cada request e resolve dinamicamente qual KB/coleção consultar — os agentes em si são os mesmos para todos os tenants.
  - ✅ **Resolvido no `agents`**: o `thread_id` do checkpoint agora é `"{tenant_id}:{contact_phone_number}"` (isola checkpoint, debounce no Redis e docs de usuário no RAG por tenant), e as credenciais do WhatsApp são por tenant, recebidas em cada request (`phone_number_id` + `access_token`, resolvidas/descriptografadas pelo `api` a partir de `whatsapp_numbers`). O Chatwoot foi removido.
- **Super-admin (plataforma)**: o painel `/admin` precisa ler dados agregados de todos os tenants, portanto opera fora do filtro por `tenant_id` — via um papel de banco com `BYPASSRLS` ou queries agregadas dedicadas que não setam `app.tenant_id`. Esse acesso é auditado (ver Painel de Administração da Plataforma).

## Modelo de Dados (Postgres)

Tabelas principais e relacionamentos. Todas as tabelas marcadas como "tenant-scoped" têm `tenant_id` (RLS aplicado — ver seção Multi-tenancy). `tenants` e `credit_packages` são globais (não tenant-scoped).

### `tenants` (escritórios — global)
- `id` (uuid, PK)
- `name`
- `cnpj` (unique, nullable)
- `email_contato`
- `credit_balance` (integer, cache do saldo — fonte da verdade é o ledger em `credit_transactions`, mas mantemos essa coluna pra leitura rápida, atualizada na mesma transação de cada lançamento)
- `status` (`active` | `suspended`)
- `created_at`, `updated_at`

### `users` (tenant-scoped)
- `id` (uuid, PK)
- `tenant_id` (FK → `tenants`)
- `name`
- `email` (**unique globalmente** — 1 e-mail = 1 conta em toda a plataforma; simplifica o login, que continua sendo apenas e-mail + senha, sem precisar identificar o tenant antes)
- `password_hash`
- `role` — mínimo viável por agora (`admin`); refinamento de papéis (ex: atendente, com permissões restritas) fica como pendência futura
- `created_at`

### `whatsapp_numbers` (tenant-scoped, 1:1 com tenant)
- `id` (uuid, PK)
- `tenant_id` (FK → `tenants`, `UNIQUE`)
- `phone_number_id` (Meta — `UNIQUE`, é a chave de resolução do webhook)
- `waba_id`
- `display_phone_number`
- `access_token_encrypted`
- `status` (`connected` | `disconnected`)
- `connected_at`

### `knowledge_base_files` (tenant-scoped)
- `id` (uuid, PK)
- `tenant_id` (FK → `tenants`)
- `filename`
- `size_bytes`
- `mime_type`
- `status` (`processing` | `ready` | `error`)
- `error_message` (nullable)
- `uploaded_at`

### `conversations` (tenant-scoped)
- `id` (uuid, PK)
- `tenant_id` (FK → `tenants`)
- `contact_phone_number`
- `state` (`agent` | `human`)
- `last_message_at`
- `created_at`
- `UNIQUE (tenant_id, contact_phone_number)` — uma conversa por contato por tenant, espelha o `thread_id` do checkpoint no `agents`

### `messages` (tenant-scoped)
- `id` (uuid, PK)
- `conversation_id` (FK → `conversations`)
- `tenant_id` (FK → `tenants`, denormalizado — facilita filtro/RLS direto na tabela sem join)
- `sender_type` (`agent` | `human` | `contact`)
- `content` (text)
- `media_url` (nullable — hoje guarda o media ID da Meta; download da mídia é pendência)
- `media_type` (nullable)
- `wa_message_id` (nullable, unique — wamid da Meta, dedup de retries do webhook)
- `tokens_used` (nullable, integer — para cálculo de crédito)
- `credits_consumed` (nullable, numeric)
- `created_at`
- Índice composto `(tenant_id, created_at)` para as queries do painel de conversas

### `platform_admins` (global — administração da plataforma)
> Usuários da **empresa fornecedora** (você), não pertencem a nenhum tenant. Tabela separada de `users` de propósito: super-admin vê métricas agregadas de toda a plataforma e nunca deve se confundir com um usuário de escritório.
- `id` (uuid, PK)
- `name`
- `email` (unique globalmente)
- `password_hash`
- `role` (`superadmin` — por ora só leitura; papéis com ações virão depois)
- `created_at`

### `credit_packages` (global)
- `id` (uuid, PK)
- `name`
- `price_brl` (numeric)
- `credits_granted` (integer)
- `active` (bool)

### `credit_transactions` (tenant-scoped — ledger/auditoria)
- `id` (uuid, PK)
- `tenant_id` (FK → `tenants`)
- `type` (`purchase` | `consumption` | `refund` | `bonus`)
- `amount_credits` (integer — positivo em `purchase`/`bonus`, negativo em `consumption`)
- `related_message_id` (FK → `messages`, nullable — rastreia consumo até a mensagem/execução que gerou)
- `credit_package_id` (FK → `credit_packages`, nullable — preenchido em `purchase`)
- `stripe_payment_id` (nullable)
- `description`
- `created_at`

### Relacionamentos (resumo)
```
tenants 1───N users
tenants 1───1 whatsapp_numbers
tenants 1───N knowledge_base_files
tenants 1───N conversations 1───N messages
tenants 1───N credit_transactions
credit_packages 1───N credit_transactions (quando type = purchase)
messages 1───N credit_transactions (quando type = consumption, via related_message_id)
```

### Migrations
- **Alembic** (Python), rodando como step do `deploy.yml` antes de subir o `api` (já mencionado em CI/CD).

### Pendências do modelo de dados
- [ ] Papéis/permissões de `users` além de `admin` (ex: papel de atendente).
- [ ] RLS só tem efeito para papéis de banco que não sejam donos das tabelas — produção deve conectar com um papel dedicado sem ownership/`BYPASSRLS` (hoje a aplicação conecta como owner, então as policies criadas na migration `0001` são inertes até isso).

## Autenticação — ✅ implementada no `api`

- JWT customizado (HS256, `pyjwt`), emitido pelo `api` (FastAPI). Senhas com `bcrypt` direto (sem passlib — incompatível com bcrypt>=4.1).
- Fluxo:
  1. `POST /api/v1/auth/login` → valida credenciais (comparação com hash dummy para e-mail inexistente — evita enumeração por timing; tenant suspenso → 403) → retorna access + refresh token.
  2. Next.js guarda o token em cookie `httpOnly` + `secure` (lado `web`, a implementar).
  3. Toda request autenticada passa pela dependency `get_current_tenant` (`app/api/deps.py`), que decodifica o JWT (`type=access`) e injeta `user_id`/`tenant_id`/`role` no contexto. Para rotas tenant-scoped, usar `get_tenant_session`, que também seta `app.tenant_id` na transação (ativa as policies de RLS).
  4. `POST /api/v1/auth/refresh` com **rotação**: o `jti` do refresh usado vai para a blacklist no Redis (`auth:blacklist:{jti}`, TTL = expiração restante) e um novo par é emitido; reuso de token rotacionado → 401. `POST /api/v1/auth/logout` revoga o refresh; access tokens expiram sozinhos (vida curta, 15 min).

## Frontend (`apps/web`) — páginas e funcionalidades

Páginas principais previstas:

- **`/`** — ✅ implementada: página pública de cadastro self-service. Sem sessão, mostra os 4 pacotes de créditos (`GET /api/v1/credit-packages`) + formulário (nome do escritório, e-mail, senha — CNPJ e verificação de e-mail ficam de fora desta entrega); submit chama `POST /api/v1/signup/checkout` (server action, chamada direto em `API_URL`, sem passar pelo proxy) e redireciona pro Checkout hospedado da Stripe. Com sessão, o middleware redireciona pra `/conversas` (comportamento preservado). `/cadastro/sucesso` faz polling em `GET /api/v1/signup/status` até a conta ficar pronta (nunca mostra erro, mesmo em timeout — o pagamento já foi aprovado pela Stripe nesse ponto) e linka pro `/login`; `/cadastro/cancelado` é estática. Ver seção Billing / Créditos para o fluxo completo (o que acontece no backend após o pagamento).
- **`/login`** — ✅ implementada: autenticação do escritório (JWT, ver seção Autenticação); server action troca credenciais por tokens e grava cookies `httpOnly`.
- **`/rom`** — página inicial pós-login (dashboard, com sessão ativa), com visão geral/informações gerais do escritório — não confundir com a página pública `/` (cadastro, sem sessão).
- **`/base-de-conhecimento`** — ✅ implementada: gestão da base de conhecimento própria do escritório.
  - ✅ **API pronta** (`/api/v1/knowledge-base/files`, autenticada e tenant-scoped): `POST` upload (multipart, PDF/DOCX/TXT — extensão é a fonte da verdade, mime genérico aceito), `GET` lista (paginado, por `uploaded_at`), `DELETE /{id}` exclusão (recusa com 409 durante `processing`). Upload grava o arquivo no volume compartilhado `kb_uploads` (`{tenant_id}/{file_id}`), registra `knowledge_base_files` com `status=processing` e enfileira `ingest_knowledge_base_file` no Arq **após o commit**.
  - ✅ **Limites**: 20 MB por arquivo (`KB_MAX_FILE_SIZE_BYTES`) e 500 MB de storage por tenant (`KB_MAX_TOTAL_SIZE_BYTES`), ambos configuráveis por env — variação por plano fica como pendência futura.
  - ✅ **Nome duplicado**: rejeitado com `409` (unique constraint `(tenant_id, filename)` como backstop de corrida entre uploads concorrentes) — o usuário exclui o arquivo antigo antes de re-subir; sem versionamento.
  - ✅ **Ingestão assíncrona** (`worker`/Arq): lê o arquivo do volume, chama `api_rag` (`doc_id` = id do registro, `conversation_id` reservado `"kb"` — ver seção RAG Service) e atualiza `status` → `ready`/`error` (com `error_message` legível, retry com backoff em erro transiente).
  - ✅ **Front pronto em `/base-de-conhecimento`**: upload com validação client-side (extensão/tamanho), listagem com badge de status (`processando`/`pronto`/`erro`, latão/verde/vermelho), polling condicionado a haver arquivo `processing`, exclusão com confirmação (desabilitada durante `processing`).
  - ✅ Os agentes já consultam essa base nas conversas — ver tool `buscar_base_conhecimento_escritorio` na seção Agents Service.
- **`/billing`** (ou `/creditos`) — gestão de pagamento e créditos **para escritórios já cadastrados** (comprar mais créditos depois do cadastro inicial) — ainda não implementada; a integração com a Stripe em si já existe (ver `/` e seção Billing / Créditos), falta só essa tela de recompra.
  - Pagamento via **Stripe**.
  - Modelo de **créditos**: o escritório compra créditos na plataforma, e o consumo dos agentes debita desse saldo (ver seção Billing / Créditos para a regra completa).
- **Painel de Conversas** (`/conversas`) — funcionalidade central do produto:
  - Lista de conversas em andamento (por canal — ex: WhatsApp).
  - Visualização em tempo real das conversas acontecendo.
  - **Takeover humano**: o usuário do escritório pode interromper o agente de IA e responder diretamente na conversa.
    - Precisa de um estado de conversa (`agent` | `human`) refletido no backend.
    - Enquanto em modo `human`, o `agents` service não deve responder automaticamente.
    - A definir: como/quando a conversa retorna para o agente (ação manual de "devolver pro agente"? timeout?).
  - ✅ **API pronta** (`/api/v1/conversations`, autenticada e tenant-scoped): `GET` lista conversas (paginado, por `last_message_at`), `GET /{id}/messages` histórico, `PATCH /{id}` toggle `agent|human` (mesma flag consultada pelo worker), `POST /{id}/messages` resposta humana — exige modo `human` (409 caso contrário), envia via Graph API com o token do tenant e persiste com `sender_type=human`.
  - ✅ **Front pronto em `/conversas`**: lista + thread com polling (5s/4s — "tempo real" via polling por ora; WebSocket/SSE fica como evolução), toggle de takeover e composer de resposta manual (habilitado só em modo `human`). O browser fala com o `api` através do proxy `/api/backend/*` do Next (cookies httpOnly + refresh transparente). A mecânica de retorno pro agente é o botão "Devolver ao agente" (`PATCH` de volta pra `agent`).

## Painel de Administração da Plataforma (`apps/web`, rota `/admin`)

Área de **back-office da empresa fornecedora** (você), separada do painel dos escritórios. Acesso restrito a `platform_admins` (tabela própria — ver Modelo de Dados), autenticado à parte dos `users` dos tenants. Rota `/admin` dentro do mesmo `apps/web` por enquanto; preparado para virar subdomínio (`admin.…`) no futuro sem refatorar o modelo.

**Escopo atual: somente leitura (dashboard de métricas).** Ações (suspender escritório, creditar manualmente) ficam como evolução futura — o modelo de dados já comporta.

Métricas previstas no dashboard:
- Total de escritórios (tenants) e ativos vs suspensos.
- Novos escritórios por período (crescimento).
- Receita: total vendido em créditos (R$) por período (agregado de `credit_transactions` tipo `purchase`).
- Créditos vendidos vs consumidos (saldo "parado" no sistema).
- Consumo agregado: mensagens processadas, execuções de agente, tokens consumidos.
- Escritórios com saldo baixo/zerado (risco de churn / oportunidade de venda).
- Números de WhatsApp conectados (quantos tenants ativaram o canal).
- Uso de base de conhecimento (nº de arquivos e storage por tenant).

> Toda leitura de dados de um tenant específico pelo super-admin deve ser auditada (log), já que atravessa o isolamento normal por `tenant_id`.

## Billing / Créditos

- Gateway de pagamento: **Stripe**.
- Modelo: pré-pago por **créditos**.
- **Valor do crédito (interno)**: 1 crédito = **R$ 0,10** (referência interna para calibrar pacotes e consumo; nunca exibido ao cliente em R$, só como saldo de créditos).

### Pacotes de compra (com bônus progressivo)

| Pacote | Preço | Créditos | Bônus |
|---|---|---|---|
| Starter | R$ 100 | 1.000 | — |
| Growth | R$ 250 | 2.750 | +10% |
| Scale | R$ 500 | 6.000 | +20% |
| Enterprise | R$ 1.000 | 13.000 | +30% |

> ✅ Esses 4 pacotes já existem no banco (seedados via migration Alembic `0003`, todos `active=true`) — não são mais valores hipotéticos. Ajustar preço/créditos depois de validar custo real de LLM/infra é só editar a linha em `credit_packages`, sem deploy novo.

### Regra de consumo — ✅ mecânica implementada (calibração pendente)
- **Não é flat por mensagem.** Consumo é calculado a partir do custo real de cada execução:
  - Tokens (input + output) consumidos na chamada ao LLM → convertidos em créditos via proporção **1 crédito = N tokens** (N calibrado para cobrir custo do provedor + margem desejada — **a definir a margem exata**).
  - Tools com custo adicional (ex: geração de documento, chamada ao Qdrant) somam um custo fixo de créditos por chamada, além do custo de tokens — **ainda não implementado**.
  - Arredondamento sempre para cima (`ceil`) — nunca cobra fração de crédito.
- **Como funciona hoje**: o `agents` devolve `tokens_used` (soma do `usage_metadata` das mensagens de IA da execução, incluindo chamadas com tool_calls) em `POST /messages`; o `worker` converte em créditos (`ceil(tokens / CREDIT_TOKENS_PER_CREDIT)`, env com default 1000 — valor de partida a calibrar), grava `tokens_used`/`credits_consumed` na primeira mensagem de resposta, lança `credit_transactions` (tipo `consumption`, negativo, `related_message_id`) e atualiza `tenants.credit_balance`, tudo na mesma transação.
- ⚠️ O saldo **pode negativar** hoje — o comportamento quando zera segue pendente (bloquear? avisar?), ver pendências de billing.
- Toda transação (compra ou consumo) é registrada em `credit_transactions` (auditoria por tenant).
- ✅ **Cadastro self-service com pagamento implementado**: o escritório escolhe um pacote em `/` (ver seção Frontend) e o `api` (`POST /api/v1/signup/checkout`) cria uma Stripe Checkout Session (modo `payment`, sem assinatura recorrente) guardando os dados do cadastro (nome, e-mail, hash da senha, pacote) na `metadata` da sessão — **nada é persistido no banco antes do pagamento confirmar**. O webhook (`POST /api/v1/webhooks/stripe`, assinatura validada via `STRIPE_WEBHOOK_SECRET`) trata `checkout.session.completed`: cria `tenant`+`user`(`role=admin`)+`credit_transactions` (tipo `purchase`) numa única transação, e atualiza `tenants.credit_balance`. Idempotente pela `id` da Checkout Session (`stripe_payment_id`) — webhook duplicado (retry da Stripe) não duplica tenant/crédito. `GET /api/v1/signup/status` é consultado pelo front (`/cadastro/sucesso`) até a conta ficar pronta.

### Pendências de billing
- [ ] Definir a margem desejada sobre o custo do LLM para calibrar o N (tokens por crédito).
- [ ] Definir custo fixo em créditos de cada tool (geração de documento, etc.).
- [ ] Comportamento quando o saldo de créditos zera (bloqueia o agente? avisa e permite negativar até X? notifica o escritório?).

## Integração WhatsApp Business

- Canal: **WhatsApp Business Platform (Cloud API)**.
- Onboarding do número: **conexão manual pelo painel** (mesmo modelo usado por Chatwoot/Chatvolt — substituiu o plano de Embedded Signup, que exigia aprovação da Meta como Tech Provider). ✅ **Implementado**: o escritório faz o setup do lado da Meta (cria/acessa um app, adiciona um System User com role Admin, gera um token de acesso permanente com `whatsapp_business_management`/`whatsapp_business_messaging`, adiciona e verifica o número) e cola as credenciais em `/configuracoes/whatsapp` no painel. O `api` (`POST /api/v1/whatsapp/connect`) valida o token/`phone_number_id` na Graph API (`GET /{phone_number_id}`, obtém o `display_phone_number`) e registra o número (`POST /{phone_number_id}/register` com o PIN de 2 fatores) **antes** de persistir qualquer credencial — nada é salvo se a Meta rejeitar. O PIN nunca é armazenado, só passa pela request. `GET /api/v1/whatsapp/connection` e `POST /api/v1/whatsapp/disconnect` completam o ciclo (número mascarado na resposta; `access_token` nunca aparece em nenhuma resposta da API).
- **1 número por escritório** (relação `tenant_id` ↔ `phone_number_id` é 1:1).
- Credenciais (access token, `phone_number_id`, `waba_id`) armazenadas de forma **criptografada** no Postgres, vinculadas ao `tenant_id`.

### Fluxo de mensagem entrante (webhook) — ✅ implementado
1. Meta envia webhook para o endpoint único da plataforma: `POST /api/v1/webhooks/whatsapp` (`GET` no mesmo path atende a verificação da Meta via `META_VERIFY_TOKEN`; assinatura `X-Hub-Signature-256` validada quando `META_APP_SECRET` setado).
2. `api` identifica o `tenant_id` a partir do `phone_number_id` recebido no payload (lookup em `whatsapp_numbers`; payloads de número desconhecido, eventos de status e wamids duplicados são ignorados com 200).
3. Mensagem é persistida no Postgres (upsert da conversa + `messages` com `sender_type=contact`) e publicada na fila Arq (`process_inbound_message`) **após o commit** — evita timeout no webhook (Meta exige resposta rápida, ~5s).
4. `worker` consome a fila e verifica o **estado da conversa**:
   - Se `agent` → descriptografa o access token (Fernet) e repassa para o `agents` service via `POST /messages`; as respostas retornadas são persistidas em `messages` (`sender_type=agent`). Erro transiente no `agents` → retry com backoff (`arq.Retry`); 202 = debounce agrupou a mensagem numa execução em andamento.
   - Se `human` → **não aciona o agente**; mensagem só aparece no Painel de Conversas esperando resposta do usuário do escritório.
5. O próprio `agents` envia a resposta ao contato via **Graph API** (`POST /{phone_number_id}/messages`) com as credenciais recebidas na request; envio pelo humano (takeover via painel) ainda não implementado.

### Fluxo de mensagem saindo (agente ou humano)
- Mesma rota de envio para ambos os casos (agente ou takeover humano), diferenciando apenas a origem no registro da conversa (`sender_type: agent | human`).
- Suporte a texto e mídia/documentos (ex: agente gerando um PDF e enviando via WhatsApp) — usar endpoint de upload de mídia da Cloud API antes de referenciar no envio.
- Mensagens fora da janela de 24h (sem "mensagem ativa" do usuário) exigem **template pré-aprovado** pela Meta — relevante caso a plataforma queira permitir contato proativo (a definir se será usado).

### Relação com o takeover (painel de conversas)
- Tabela `conversations` com campo de estado (`agent` | `human`) e `tenant_id`.
- Toggle de takeover no painel altera esse estado e é a mesma flag consultada pelo `worker` no passo 4 acima.

### Pendências específicas do WhatsApp
- [ ] Definir se haverá suporte a mensagens template (contato proativo) ou só reativo (dentro da janela de 24h).
- [ ] Rate limits da Cloud API por número — throttling na fila de envio.
- [ ] Retry/dead-letter para falhas de envio.

## Agents Service (`apps/agents`)

> Código real, incorporado de um projeto standalone anterior (single-tenant). Documentação técnica completa em `apps/agents/API_AGENTS.md` — consultar/atualizar esse arquivo quando o código mudar. Resumo das features abaixo; ⚠️ marca o que precisa de adaptação para caber na visão multi-tenant deste `CLAUDE.md`.

**O que já existe e funciona:**
- Microserviço **FastAPI** interno: recebe mensagens já resolvidas pelo `api` via `POST /messages` (contrato: `tenant_id`, `contact_phone_number`, `message`, `attachments`, `phone_number_id`, `access_token`; auth de serviço via header `Authorization: <AGENTS_API_KEY>`), faz **debounce de rajada** via Redis (agrupa mensagens próximas de um mesmo cliente por ~5s), roda um **grafo LangGraph** e envia a(s) resposta(s) ao cliente direto pela **Graph API da Meta** (`clients/whatsapp.py`), com as credenciais do tenant recebidas na request. Retorna as respostas ao chamador para persistência em `messages` e contabilização de créditos.
- Grafo composto por uma **secretária de triagem** (`agente_secretaria`) + três **especialistas fixos**: `agente_condominial`, `agente_contratos`, `agente_direito_consumidor`. A secretária faz a triagem inicial e transfere para o especialista certo via tool `transfer_to_specialist`; a partir daí a conversa fica fixada nesse especialista (persistido no checkpoint).
- Estado da conversa (histórico de mensagens, especialista fixado) persistido em **Postgres** via `AsyncPostgresSaver` do LangGraph — o `thread_id` do checkpoint é hoje o `conversation_id` do Chatwoot.
- Tools de RAG (`bucar_base_conhecimento_condominial/contratos/direito_consumidor`, `bucar_base_conhecimento_usuario`, `buscar_base_conhecimento_escritorio`) chamam o `api_rag` via HTTP (`RAG_API_URL`) para buscar na base do sistema (por categoria), na base de documentos do próprio usuário/conversa, ou na base de conhecimento do escritório (`conversation_id` reservado `"kb"` — ver seção RAG Service e "Frontend"/`/base-de-conhecimento`). Bindada aos 4 agentes (secretária + 3 especialistas). O `tool_node` injeta o `conversation_id` do estado do grafo nas tools escopadas por tenant (`STATE_SCOPED_TOOLS`), **nunca** confiando no valor gerado pelo LLM — isolamento multi-tenant.
- Sanitização de histórico (`strip_messages`) antes de cada chamada ao LLM: fecha `tool_calls` pendentes (evita erro da OpenAI) e recorta às últimas N mensagens sem quebrar um bloco de tool no meio.
- Observabilidade via **Langfuse** (tracing) + **Loguru** (log estruturado, rotação em arquivo se `LOG_FILE` setado).
- Endpoints: `POST /` (webhook), `GET /agents` (lista agentes/tools disponíveis, para dashboards), `DELETE /conversations/{thread_id}` (apaga histórico de uma conversa).

**⚠️ O que precisa de adaptação para multi-tenancy (antes de produção nesta plataforma):**
- ✅ **Chatwoot removido (feito)**: `POST /` (payload do Chatwoot) virou `POST /messages` com contrato interno; `clients/chatwoot.py` substituído por `clients/whatsapp.py` (Graph API, `send_text_message` + `send_document_message` por link); `thread_id` do checkpoint e chaves de debounce no Redis agora escopados como `"{tenant_id}:{contact_phone_number}"`; envs do Chatwoot removidas (novas: `AGENTS_API_KEY`, `GRAPH_API_BASE_URL`, `GRAPH_API_VERSION`). Cobertura em `tests/unit/test_routes.py`.
- ✅ O lado do `api`/`worker` existe: webhook Meta → resolve tenant → fila → `POST /messages`. O que falta para exercitar de ponta a ponta é o Embedded Signup (ou inserção manual do número/token cifrado em `whatsapp_numbers`).
- Upload de mídia da Cloud API (para enviar documento gerado por tool sem depender de URL pública) ainda não implementado — `send_document_message` hoje só envia por link.
- **Os 3 especialistas são hoje hardcoded para o nicho de um único escritório** (condominial, contratos, direito do consumidor). Avaliar se esse é o conjunto fixo de agentes para *toda* a plataforma (compatível com "agentes fixos definidos pela plataforma") ou se precisa generalizar.
- Sem integração com o **estado `agent`/`human`** de takeover do painel de conversas — hoje sempre responde automaticamente.
- ✅ **Consumo de créditos instrumentado**: `POST /messages` devolve `tokens_used` da execução; o `worker` converte em créditos e lança em `messages`/`credit_transactions`/`tenants.credit_balance` (ver "Regra de consumo"). Falta o custo fixo por tool.
- Débitos técnicos conhecidos (ver §11 de `API_AGENTS.md`): `ENDPOINT_URL`/`API_KEY`/`CONVERSATION_ID` hardcoded em `agents/tools.py`; tools de geração de documento citadas nos prompts (`fazer_contrato`, `enviar_arquivo`) não estão implementadas (só existe `enviar_documento`, e ele não está bindado a nenhum agente); despedida de transferência automática só implementada para secretária/condominial.

## RAG Service (`apps/api_rag`)

> Código real, incorporado de um projeto standalone anterior (single-tenant). Documentação técnica completa em `apps/api_rag/API.md` — consultar/atualizar esse arquivo quando o código mudar. É o serviço que os agentes chamam para consultar (e, no futuro, gerenciar) a base de conhecimento.

**O que já existe e funciona:**
- Microserviço **FastAPI** para **ingestão** de documentos (PDF/DOCX/TXT) e **retrieval híbrido**: embedding **denso** (OpenAI `text-embedding-3-small`) + **esparso** (API HTTP própria), fundidos por **RRF** no Qdrant, com expansão de query via **HyDE** (parágrafo hipotético) + extração de keywords (ambos gerados por LLM).
- Ingestão: extrai texto do arquivo → chunking (`chonkie`) → gera embeddings denso/esparso por chunk → salva o arquivo cru em disco → grava metadados no Postgres (`documentos_usuario`/`documentos_sistema`) → upsert no Qdrant.
- Duas bases lógicas: **sistema** (base de conhecimento da plataforma, compartilhada, indexada sob o tenant reservado `"system"`, com rastreio de origem via `id_drive`) e **usuário** (documentos por tenant + conversa).
- Endpoints: `POST/DELETE /documents/{system,users}/{insert,delete}`, `POST /retrieval/{system,users}`, `GET /health`.
- Migrations com **Alembic** (mesma ferramenta já prevista para o `api` geral neste `CLAUDE.md`).
- Comandos: `uv run pytest tests/unit`, `uv run ruff check .`, `uv run alembic upgrade head` (dentro de `apps/api_rag`).

**✅ Retrofit multi-tenant feito (2026-07):**
- **Collection única** (`QDRANT_COLLECTION`, provisionada automaticamente no startup com vetores `dense`+`sparse` e índices de payload) com **`tenant_id` obrigatório na camada de acesso**: busca/deleção sem `tenant_id` levantam erro, upsert rejeita ponto sem `tenant_id` no payload. Contrato das rotas atualizado (`/retrieval/users` e `/documents/users/*` exigem `tenant_id`; `/documents/system/insert` usa `base` de verdade no form). O client do `agents` (`clients/retrieval.py`) divide o `thread_id` composto e envia `tenant_id`+`conversation_id`.
- **Bugs do §9 do `API.md` corrigidos**: mismatch `text`/`texto` no retrieval, fluxo de delete (métodos inexistentes + `doc.doc_id`), typo `fild`→`field`, typo `convesation_id` no form, sparse embedding síncrono → `httpx` async, `TOP_K`/`PREFETCH_K` como int, migration inicial vazia preenchida (cadeia Alembic funciona em banco novo).
- Coluna `tenant_id` em `documentos_usuario` (migration `a1b2c3d4e5f6`; nullable por causa de linhas legadas).

**✅ Suporte à base de conhecimento do escritório (2026-07):**
- `.txt` aceito na ingestão (decodificação UTF-8 com fallback latin-1), além de PDF/DOCX.
- `POST /documents/users/insert` aceita `doc_id` opcional (UUID) — quando presente, usado como PK de `documentos_usuario` e no payload do Qdrant; re-ingestão com o mesmo `doc_id` deleta o documento anterior antes de gravar o novo (idempotência para retries do `worker`), com a deleção rodando **antes** da gravação do arquivo novo (evita apagar o arquivo recém-escrito quando os paths coincidem).
- `conversation_id` reservado **`"kb"`**: usado pelo `api`/`worker` do monorepo para a base de conhecimento de cada escritório, reaproveitando `/documents/users/*` e `/retrieval/users` sem rotas novas — mesmo padrão do tenant reservado `"system"`.

**⚠️ O que ainda falta neste serviço:**
- **Decisão tomada**: a autenticação continua por API key única (`API_KEY`) como **serviço interno** — só `agents`/`api`/`worker` chamam, nunca exposto direto ao escritório. O isolamento depende de o chamador enviar o `tenant_id` correto.
- Dados indexados antes do retrofit (collections antigas `COLLECTION_SISTEMA`/`COLLECTION_USERS`) ficam invisíveis — precisam ser **re-ingeridos** na collection única; linhas legadas de `documentos_usuario` estão com `tenant_id` NULL.
- Sem custo em créditos instrumentado (ingestão e retrieval não geram `credit_transactions`).

## Testes

Cada app tem sua própria pasta de testes, isolada e específica:

```
apps/
  web/
    __tests__/            # ou tests/, espelhando a estrutura de src/
  api/
    tests/
      unit/                # testes de função/unidade, com dados mockados
      integration/         # testes que tocam Postgres/Redis (via containers de teste)
  agents/
    tests/
      unit/                # nodes/tools do LangGraph testados isoladamente, com mocks de LLM/Qdrant
      integration/
  worker/
    tests/
      unit/
      integration/
```

- **Python (`api`, `agents`, `worker`)**: `pytest` + `pytest-mock` / `unittest.mock` para mocks; `pytest-asyncio` (stack é async). Nomenclatura: `test_*.py`, funções `def test_*`.
- **Frontend (`web`)**: `Vitest` para unit/component tests; nomenclatura `*.test.ts(x)`, mocks de chamadas de API via `msw` (Mock Service Worker).
- **Regra geral**: testes unitários mockam dependências externas (LLM, Qdrant, Postgres, WhatsApp Cloud API); testes de integração usam containers reais (via `docker-compose.test.yml` ou `testcontainers`).
- **Cobertura mínima**: a definir — sugestão inicial de rodar cobertura no CI só como relatório (não bloquear PR) até o time definir um piso.

## CI/CD

Pipeline em **GitHub Actions**, pensado para o monorepo com múltiplos apps/containers. Estrutura sugerida:

```
.github/
  workflows/
    ci.yml            # lint + testes, roda em toda PR
    build-images.yml  # build e push das imagens Docker (main/tags)
    deploy.yml         # deploy via docker-compose no servidor
```

### `ci.yml` (Pull Request)
1. Detecta quais apps mudaram (usar `turborepo` cache/filtro ou `paths-filter`) — evita rodar tudo a cada PR.
2. Para cada app afetado:
   - `web`: `pnpm lint` + `pnpm test` (Vitest).
   - `api` / `agents` / `worker`: `ruff check` + `ruff format --check` + `pytest`.
3. Falha o PR se qualquer etapa falhar.

### `build-images.yml` (merge na `main` ou tag)
1. Build da imagem Docker de cada app alterado (`web`, `api`, `agents`, `worker`).
2. Push para um registry (ex: GitHub Container Registry — `ghcr.io`), taggeado com o SHA do commit (+ `latest` na main).

### `deploy.yml`
1. Conecta ao servidor (SSH action) — **VPS próprio, Ubuntu Linux**, onde o `docker-compose.yml` de produção está.
2. Faz `docker compose pull` (novas imagens) + `docker compose up -d` (recria só os serviços que mudaram).
3. Roda migrations do Postgres (Alembic) como step antes de subir o `api`.

> Nota: esse é um ponto de partida simples e direto pra funcionar com a stack em Docker Compose que já definimos, rodando num único VPS. Se o projeto crescer (múltiplos servidores, necessidade de zero-downtime, auto-scaling), vale reavaliar — mas isso é decisão futura, não bloqueia o início.

### Pendências de CI/CD e testes
- [ ] Definir cobertura mínima de testes (se/quando bloquear PR).
- [ ] Definir onde roda o Postgres/Redis/Qdrant de teste no CI (containers de serviço do próprio GitHub Actions ou testcontainers).
- [ ] Gestão de secrets (tokens do WhatsApp, Stripe, JWT secret) — GitHub Secrets + `.env` no VPS.
- [ ] Acesso SSH do GitHub Actions ao VPS (chave dedicada, usuário com permissão restrita a `docker compose`, não root).
- [ ] Configuração do `cloudflared` no VPS (fora do Docker) apontando para a porta externa exposta pelos serviços.

## Logging / Observabilidade

- Cada app loga em **`stdout`/`stderr`**, em formato **JSON estruturado** (`timestamp`, `level`, `service`, `tenant_id` quando aplicável, mensagem).
- **Rotação de log via Docker**, não customizada na aplicação: cada serviço no `docker-compose.yml` usa o logging driver `json-file` com `max-size: "10m"` e `max-file: "5"` — arquivo roda automaticamente ao chegar em 10MB, mantendo os últimos 5 arquivos por serviço.
- Sem agregador de logs por enquanto (ex: Grafana Loki) — item futuro, não bloqueia o início.

## Convenções de código

### Nomenclatura
- **TypeScript/Next.js**: componentes em `PascalCase` (`AgentCard.tsx`); hooks em `camelCase` com prefixo `use` (`useTenantContext.ts`); pastas em `kebab-case`.
- **Python/FastAPI**: módulos/arquivos em `snake_case`; classes em `PascalCase`; funções/variáveis em `snake_case`.
- **Banco de dados**: tabelas em `snake_case` plural (`tenants`, `whatsapp_numbers`, `credit_transactions`); toda tabela multi-tenant com `tenant_id`.
- **Rotas de API**: `/api/v1/{recurso}` em `kebab-case`, sempre versionadas.
- **Branches**: `feature/`, `fix/`, `chore/` + `kebab-case`.
- **Commits**: Conventional Commits (`feat:`, `fix:`, `refactor:`...).

### Lint e formatação
- Frontend: ESLint + Prettier.
- Backend: Ruff (lint + format).

## Infraestrutura (Docker Compose)

Serviços no compose da raiz: `web`, `api`, `agents`, `api_rag`, `worker`, `postgres`, `qdrant`, `redis`.
Volumes nomeados para persistência: `postgres_data`, `qdrant_data`, `redis_data`, `rag_uploads` (arquivos crus do RAG), `kb_uploads` (arquivos temporários da base de conhecimento, compartilhado entre `api` e `worker` — o `api` grava, o `worker` lê e apaga após ingerir no `api_rag`). Volumes nomeados vivem no storage do Docker (`/var/lib/docker/volumes/advoxs_*`), não na pasta do repo.

- **Postgres: instância única, um database por serviço** — `advoxs` (api/worker, negócio + RLS), `advoxs_agents` (checkpoints do LangGraph) e `advoxs_rag` (metadados de documentos). Roles e databases criados por `infra/postgres/init/002-databases.sh` na primeira subida do volume; cada serviço conecta com usuário próprio e `CONNECT` revogado de `PUBLIC` (um serviço comprometido não lê o database dos outros). Senhas: `AGENTS_DB_PASSWORD`/`RAG_DB_PASSWORD` no `.env`.
- **Portas padronizadas**: web 3000, api 8000, agents 8001, api_rag 8002 (host, via override; internamente o api_rag escuta 8000).
- ⚠️ `apps/agents/docker-compose.yml` e `apps/api_rag/docker-compose.yml` são **composes legados** dos projetos standalone (sobem postgres/redis/qdrant próprios e colidem com as portas do compose da raiz) — servem só para rodar o microserviço isolado; candidatos a remoção quando a integração estiver completa.

- Hospedagem: **VPS próprio (Ubuntu Linux)**.
- Exposição/HTTPS: **Cloudflare Tunnel (`cloudflared`)** rodando **direto no VPS** (fora do Docker Compose), apontando para a porta externa que os serviços (`web`/`api`) expõem no host. Não há reverse proxy próprio (Nginx/Caddy) nem serviço `cloudflared` dentro do `docker-compose.yml`.
  - Reavaliar reverse proxy próprio se, no futuro, for necessário algo que o Cloudflare Tunnel não cubra bem (ex: regras de roteamento muito específicas entre os serviços).

## Pendências / próximos tópicos a detalhar

Itens ainda em aberto, que não bloqueiam o início do desenvolvimento:

- [ ] Papéis/permissões de `users` além de `admin` (ex: papel de atendente).
- [x] ~~Extensões de arquivo suportadas na base de conhecimento, limite total de storage por tenant, status de ingestão no front e comportamento de nome duplicado~~ (feito — ver "Frontend"/`/base-de-conhecimento`: PDF/DOCX/TXT, 20 MB/arquivo, 500 MB/tenant, badges `processando`/`pronto`/`erro`, duplicado → 409). Segue pendente: variação do limite por plano, botão "reprocessar" para arquivos em `error`, custo em créditos de ingestão/retrieval.
- [ ] Mecânica de retorno da conversa de `human` para `agent` (ação manual? timeout?).
- [ ] Calibragem da margem sobre custo de LLM (N tokens por crédito) e custo fixo em créditos de cada tool.
- [ ] Comportamento quando o saldo de créditos zera.

### Retrofit de `apps/agents` e `apps/api_rag` para multi-tenancy (bloqueia produção, não bloqueia início do dev)

Os dois microserviços já existem (ver seções "Agents Service" e "RAG Service") mas foram construídos single-tenant. Antes de atender tenants reais nesta plataforma:

- [x] ~~Remover Chatwoot do `agents` e migrar para Meta Cloud API direta~~ (feito — ver "Agents Service").
- [x] ~~Webhook da Meta no `api` + processamento no `worker`~~ (feito — ver "Fluxo de mensagem entrante").
- [x] ~~Onboarding do número (Embedded Signup)~~ (feito com um modelo mais simples — conexão manual pelo painel em `/configuracoes/whatsapp`, sem exigir aprovação da Meta como Tech Provider; ver "Integração WhatsApp Business").
- [x] ~~Propagar `tenant_id` no `agents`~~ (feito — `thread_id` composto por tenant no checkpoint/debounce/RAG de usuário).
- [x] ~~Definir e propagar `tenant_id` no `api_rag` + unificar as collections~~ (feito — collection única com `tenant_id` obrigatório na camada de acesso; ver "RAG Service". ⚠️ dados antigos precisam ser re-ingeridos).
- [x] ~~Auth do `api_rag`~~ (decisão: continua API key única como serviço interno, nunca exposto direto ao escritório).
- [x] ~~Corrigir bugs conhecidos do `api_rag`~~ (feito — mismatch `text`/`texto`, delete quebrado, typo `fild`→`field`, sparse síncrono, migration inicial vazia; ver §9 de `API.md`).
- [ ] Avaliar se os 3 especialistas hardcoded do `agents` (condominial, contratos, direito do consumidor) são o conjunto fixo de agentes de toda a plataforma ou precisam generalizar.
- [ ] Remover credenciais/URL hardcoded em `agents/tools.py` (`ENDPOINT_URL`/`API_KEY`/`CONVERSATION_ID` da tool `enviar_documento`).
- [x] ~~Instrumentar consumo de créditos por tokens~~ (feito — `agents` devolve `tokens_used`, `worker` debita; ver "Regra de consumo"). Falta: custo fixo por tool e consumo do `api_rag` (ingestão/retrieval).
- [ ] Rotacionar os segredos reais presentes nos `.env` trazidos junto com esses dois projetos.

(Ver também "Pendências específicas do WhatsApp", "Pendências de billing", "Pendências do modelo de dados" e "Pendências de CI/CD e testes" nas seções acima.)
