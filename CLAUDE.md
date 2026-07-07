# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Guia de contexto e convenções do projeto para o Claude Code e demais colaboradores.

## Estado atual do repositório

Este repositório está em fase de planejamento: contém apenas este `CLAUDE.md` (spec de produto/arquitetura), sem código-fonte ainda. Não existem `package.json`, `pyproject.toml`, apps ou testes implementados — portanto não há comandos de build/lint/test para documentar. Quando o código dos apps (`web`, `api`, `agents`, `worker`) for incorporado, esta seção deve ser substituída por comandos reais (setup, dev server, lint, testes) e por notas de arquitetura extraídas do código.

## Visão do produto

Plataforma **multi-tenant B2B** que fornece **agentes de IA prontos** para escritórios de advocacia.

- Cada tenant é um **escritório de advocacia**.
- Os **agentes são fixos e bem definidos pela plataforma** (não são criados/customizados pelo usuário).
- O que cada escritório pode personalizar:
  - Adicionar suas próprias **bases de conhecimento** (RAG).
  - Conectar um **número de WhatsApp Business** para que os agentes atendam clientes/contatos do escritório por lá.
- Os agentes usam **tools** (ex: geração de documentos, consulta a base de conhecimento) para executar tarefas.

## Arquitetura geral

Monorepo com múltiplos apps. O serviço de agentes é **isolado como microserviço** por receber requests de todos os tenants simultaneamente e concentrar a orquestração LangGraph.

```
apps/
  web/          # Next.js — painel do escritório (auth, gestão de KB, config WhatsApp)
  api/          # FastAPI — backend geral: tenants, usuários, billing, KB, integrações
  agents/       # FastAPI — microserviço dedicado, multi-tenant, executa os agentes (LangGraph)
  worker/       # Arq — jobs assíncronos (ingestão de KB, processamento de mensagens)
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

### Fluxo resumido
1. Escritório interage via painel (`web`) ou via WhatsApp Business (webhook → `api`).
2. `api` identifica o `tenant_id`, valida permissões e repassa a requisição para o `agents` service.
3. `agents` resolve qual agente (grafo LangGraph) deve ser executado, injeta o contexto do tenant (qual KB consultar) e executa as tools necessárias.
4. Tools acessam Qdrant (RAG), geram documentos, etc., sempre escopadas por `tenant_id`.
5. Resposta volta pela cadeia até o canal de origem (painel ou WhatsApp).

## Stack e versões

| Camada | Escolha |
|---|---|
| Frontend | Next.js 15 (App Router, RSC) |
| Backend geral | FastAPI + Python 3.12 |
| Microserviço de agentes | FastAPI + Python 3.12 |
| Orquestração de agentes | LangGraph |
| Banco relacional | PostgreSQL 16 |
| Banco vetorial | Qdrant |
| Cache / fila | Redis 7 |
| Fila de jobs assíncronos | Arq |
| Gerenciador pacotes JS | pnpm + Turborepo |
| Gerenciador pacotes Python | uv |
| Autenticação | JWT customizado no FastAPI |
| Integração de canal | WhatsApp Business (Cloud API) |
| Infra local/deploy | Docker Compose + volumes |

## Multi-tenancy

- Isolamento por **`tenant_id`** em todas as camadas.
- **Postgres**: toda tabela multi-tenant tem coluna `tenant_id` (FK indexada, `NOT NULL`). **RLS (Row-Level Security) ativado como camada extra de proteção**, além do filtro na aplicação — cada policy filtra por `tenant_id = current_setting('app.tenant_id')::uuid`; a aplicação seta essa variável de sessão a cada request. Justificativa: dado jurídico sensível, defesa em profundidade (mesmo um bug/query sem filtro não expõe dado de outro tenant).
- **Qdrant**: **collection única** com `tenant_id` como payload indexado. Todo acesso ao Qdrant passa obrigatoriamente por filtro de `tenant_id` na camada de acesso (nunca opcional/decisão do agente).
- **Agents service**: recebe `tenant_id` no contexto de cada request e resolve dinamicamente qual KB/coleção consultar — os agentes em si são os mesmos para todos os tenants.
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
- `phone_number_id` (Meta)
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

### `messages` (tenant-scoped)
- `id` (uuid, PK)
- `conversation_id` (FK → `conversations`)
- `tenant_id` (FK → `tenants`, denormalizado — facilita filtro/RLS direto na tabela sem join)
- `sender_type` (`agent` | `human` | `contact`)
- `content` (text)
- `media_url` (nullable)
- `media_type` (nullable)
- `tokens_used` (nullable, integer — para cálculo de crédito)
- `credits_consumed` (nullable, numeric)
- `created_at`

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
- [ ] Índices compostos a definir na prática (ex: `(tenant_id, created_at)` em `messages` para queries do painel de conversas).

## Autenticação

- JWT customizado, emitido pelo `api` (FastAPI).
- Fluxo:
  1. `POST /api/v1/auth/login` → valida credenciais → retorna access + refresh token.
  2. Next.js guarda o token em cookie `httpOnly` + `secure`.
  3. Toda request ao FastAPI passa por uma dependency (`get_current_tenant`) que decodifica o JWT e injeta `tenant_id`/`role` no contexto.
  4. Refresh token com rotação; revogação via blacklist no Redis.

## Frontend (`apps/web`) — páginas e funcionalidades

Páginas principais previstas:

- **`/login`** — autenticação do escritório (JWT, ver seção Autenticação).
- **`/rom`** — página inicial pós-login, com visão geral/informações gerais do escritório (dashboard).
- **`/base-de-conhecimento`** (ou `/knowledge-base`) — gestão da base de conhecimento própria do escritório:
  - Upload de arquivos (PDF, TXT, e possivelmente outros formatos — a definir extensões suportadas).
  - Listagem dos arquivos salvos.
  - Exclusão de arquivos.
  - Cada upload dispara ingestão assíncrona (via `worker`/Arq): parsing → chunking → embedding → indexação no Qdrant, sempre escopado por `tenant_id`.
  - **Limite de 20 MB por arquivo** e limite total de storage por tenant (ex: 500 MB–1 GB, possivelmente variando por plano — a definir).
  - A definir: feedback de status da ingestão pro usuário (processando / pronto / erro), versionamento (o que acontece ao re-subir um arquivo com mesmo nome).
- **`/billing`** (ou `/creditos`) — gestão de pagamento e créditos:
  - Pagamento via **Stripe**.
  - Modelo de **créditos**: o escritório compra créditos na plataforma, e o consumo dos agentes debita desse saldo (ver seção Billing / Créditos para a regra completa).
- **Painel de Conversas** (`/conversas`) — funcionalidade central do produto:
  - Lista de conversas em andamento (por canal — ex: WhatsApp).
  - Visualização em tempo real das conversas acontecendo.
  - **Takeover humano**: o usuário do escritório pode interromper o agente de IA e responder diretamente na conversa.
    - Precisa de um estado de conversa (`agent` | `human`) refletido no backend.
    - Enquanto em modo `human`, o `agents` service não deve responder automaticamente.
    - A definir: como/quando a conversa retorna para o agente (ação manual de "devolver pro agente"? timeout?).

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

> Valores de partida — ajustar depois de validar custo real de LLM/infra e margem desejada.

### Regra de consumo
- **Não é flat por mensagem.** Consumo é calculado a partir do custo real de cada execução:
  - Tokens (input + output) consumidos na chamada ao LLM → convertidos em créditos via proporção **1 crédito = N tokens** (N calibrado para cobrir custo do provedor + margem desejada — **a definir a margem exata**).
  - Tools com custo adicional (ex: geração de documento, chamada ao Qdrant) somam um custo fixo de créditos por chamada, além do custo de tokens.
  - Arredondamento sempre para cima (`ceil`) — nunca cobra fração de crédito.
- Toda transação (compra ou consumo) é registrada em `credit_transactions` (auditoria por tenant).
- Webhook do Stripe confirma pagamento → credita o saldo (`credit_transactions` tipo `purchase`).

### Pendências de billing
- [ ] Definir a margem desejada sobre o custo do LLM para calibrar o N (tokens por crédito).
- [ ] Definir custo fixo em créditos de cada tool (geração de documento, etc.).
- [ ] Comportamento quando o saldo de créditos zera (bloqueia o agente? avisa e permite negativar até X? notifica o escritório?).

## Integração WhatsApp Business

- Canal: **WhatsApp Business Platform (Cloud API)**.
- Onboarding do número: **Embedded Signup da Meta** — o escritório conecta o número diretamente no painel (`web`), sem manuseio manual de token/Phone Number ID.
  - Requer app Meta configurado como Tech Provider/Solution Partner (setup único da plataforma, não por tenant).
- **1 número por escritório** (relação `tenant_id` ↔ `phone_number_id` é 1:1).
- Credenciais (access token, `phone_number_id`, `waba_id`) armazenadas de forma **criptografada** no Postgres, vinculadas ao `tenant_id`.

### Fluxo de mensagem entrante (webhook)
1. Meta envia webhook para um endpoint único da plataforma (`api`), ex: `POST /api/v1/webhooks/whatsapp`.
2. `api` identifica o `tenant_id` a partir do `phone_number_id` recebido no payload.
3. Mensagem é persistida no Postgres (histórico da conversa) e publicada numa fila (Redis/Arq) para processamento assíncrono — evita timeout no webhook (Meta exige resposta rápida, ~5s).
4. `worker` consome a fila e verifica o **estado da conversa**:
   - Se `agent` → repassa para o `agents` service (LangGraph), que processa e retorna a resposta.
   - Se `human` → **não aciona o agente**; mensagem só aparece no Painel de Conversas esperando resposta do usuário do escritório.
5. Resposta (do agente ou do humano via painel) é enviada de volta via **Graph API** (`POST /{phone_number_id}/messages`).

### Fluxo de mensagem saindo (agente ou humano)
- Mesma rota de envio para ambos os casos (agente ou takeover humano), diferenciando apenas a origem no registro da conversa (`sender_type: agent | human`).
- Suporte a texto e mídia/documentos (ex: agente gerando um PDF e enviando via WhatsApp) — usar endpoint de upload de mídia da Cloud API antes de referenciar no envio.
- Mensagens fora da janela de 24h (sem "mensagem ativa" do usuário) exigem **template pré-aprovado** pela Meta — relevante caso a plataforma queira permitir contato proativo (a definir se será usado).

### Relação com o takeover (painel de conversas)
- Tabela `conversations` com campo de estado (`agent` | `human`) e `tenant_id`.
- Toggle de takeover no painel altera esse estado e é a mesma flag consultada pelo `worker` no passo 4 acima.

### Pendências específicas do WhatsApp
- [ ] Definir se haverá suporte a mensagens template (contato proativo) ou só reativo (dentro da janela de 24h).
- [ ] Setup do app Meta como Tech Provider (documentação/processo de aprovação).
- [ ] Rate limits da Cloud API por número — throttling na fila de envio.
- [ ] Retry/dead-letter para falhas de envio.

## Agents Service (`apps/agents`) — resumo geral (⚠️ a validar contra implementação real)

> Esta seção é um resumo do entendimento atual, construído pela conversa. Quando a implementação real do microserviço for incorporada ao repo, **revisar e corrigir** o que estiver divergente.

- Microserviço **FastAPI** isolado dos demais (`api`, `web`), dedicado exclusivamente à execução dos agentes.
- Recebe requests de **múltiplos tenants simultaneamente** (multi-tenant nativo no serviço, não uma instância por tenant).
- Orquestração via **LangGraph**.
- **Agentes são fixos e bem definidos pela plataforma** — o mesmo conjunto de agentes/grafos atende todos os escritórios. Não há criação/customização de agente pelo tenant.
- O que varia por tenant é **apenas a base de conhecimento** consultada via RAG.
- Cada request recebida traz (ou o serviço resolve a partir do contexto) o `tenant_id`, usado para:
  - Selecionar/filtrar a base de conhecimento correta no Qdrant (payload filter obrigatório por `tenant_id`).
  - Aplicar qualquer regra de negócio específica do tenant (ex: consumo de créditos).
- **Tools** disponíveis aos agentes incluem, no mínimo:
  - Geração de documentos.
  - Consulta à base de conhecimento (RAG, sempre escopada por tenant).
- Respeita o estado da conversa definido pelo painel (`agent` | `human`): se a conversa estiver em modo `human` (takeover), o serviço não deve gerar resposta automática.
- Entrada/saída do serviço se dá via chamadas do `api` (que já resolveu autenticação/tenant antes de repassar) — o `agents` service não lida com login/JWT de usuário final diretamente.

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

Serviços previstos: `web`, `api`, `agents`, `worker`, `postgres`, `qdrant`, `redis`.
Volumes nomeados para persistência: `postgres_data`, `qdrant_data`, `redis_data`.

- Hospedagem: **VPS próprio (Ubuntu Linux)**.
- Exposição/HTTPS: **Cloudflare Tunnel (`cloudflared`)** rodando **direto no VPS** (fora do Docker Compose), apontando para a porta externa que os serviços (`web`/`api`) expõem no host. Não há reverse proxy próprio (Nginx/Caddy) nem serviço `cloudflared` dentro do `docker-compose.yml`.
  - Reavaliar reverse proxy próprio se, no futuro, for necessário algo que o Cloudflare Tunnel não cubra bem (ex: regras de roteamento muito específicas entre os serviços).

## Pendências / próximos tópicos a detalhar

Itens ainda em aberto, que não bloqueiam o início do desenvolvimento:

- [ ] **Tools dos agentes e RAG** — contrato e registro no LangGraph (será trazido pela implementação já existente do microserviço `agents`, ainda por incorporar).
- [ ] Papéis/permissões de `users` além de `admin` (ex: papel de atendente).
- [ ] Extensões de arquivo suportadas na base de conhecimento além de PDF/TXT, e limite total de storage por tenant.
- [ ] Fluxo de status de ingestão de documentos (processando/pronto/erro) exibido no front.
- [ ] Comportamento ao re-subir arquivo com nome duplicado (versionamento ou substituição).
- [ ] Mecânica de retorno da conversa de `human` para `agent` (ação manual? timeout?).
- [ ] Calibragem da margem sobre custo de LLM (N tokens por crédito) e custo fixo em créditos de cada tool.
- [ ] Comportamento quando o saldo de créditos zera.

(Ver também "Pendências específicas do WhatsApp", "Pendências de billing", "Pendências do modelo de dados" e "Pendências de CI/CD e testes" nas seções acima.)
