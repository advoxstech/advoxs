# API Agent — Documentação Técnica Completa

> Serviço de atendimento jurídico automatizado via WhatsApp (Meta Cloud API),
> construído sobre **LangGraph** (multi-agente) + **FastAPI**. Este documento
> descreve a arquitetura, o fluxo de execução, os contratos de entrada/saída e
> as dependências externas. É a **fonte da verdade** sobre o comportamento
> atual do serviço — manter atualizado quando o código mudar.

---

## 1. Visão geral

O serviço é um **microserviço interno** da plataforma: recebe mensagens já
resolvidas pelo backend geral (`api`), que identificou o tenant a partir do
`phone_number_id` do webhook da Meta e descriptografou as credenciais do
WhatsApp do tenant. O serviço agrupa mensagens em rajada (debounce via
**Redis**), encaminha para um grafo de agentes de IA (**LangGraph**), persiste
o estado da conversa (**PostgreSQL**) e envia a(s) resposta(s) ao cliente
diretamente pela **WhatsApp Cloud API** (Graph API), usando as credenciais do
tenant recebidas na request.

O grafo é composto por uma **secretária de triagem** e três **agentes
especialistas** jurídicos. A secretária conduz a triagem inicial e transfere a
conversa para o especialista correto. A partir daí, todas as mensagens da mesma
conversa vão direto para o especialista fixado no estado.

```
Cliente (WhatsApp) ──▶ Meta ──webhook──▶ api (backend geral)
                                          │ resolve tenant_id + credenciais
                                          ▼
                              POST /messages ──▶ FastAPI (api/routes.py)
                                                      │
                                                      ▼
                                            debounce (Redis, 5s)
                                                      │
                                                      ▼
                                  run_agent (LangGraph + Postgres checkpoint)
                                                      │
                                            ┌─────────┴──────────┐
                                            ▼                    ▼
                                      agente_secretaria    especialista fixado
                                            │                    │
                                            └──── tool_node ─────┘
                                                      │
                                                      ▼
                              respostas ──▶ WhatsAppClient.send_text_message
                                            (Graph API, credenciais do tenant)
```

### Stack

| Camada             | Tecnologia                                        |
|--------------------|---------------------------------------------------|
| API HTTP           | FastAPI + Uvicorn                                 |
| Orquestração agent | LangGraph 1.2+ (`StateGraph`, `Command`)          |
| LLM                | OpenAI `gpt-5-mini-2025-08-07` via `langchain-openai` |
| Persistência conv. | PostgreSQL (`AsyncPostgresSaver` checkpointer)    |
| Buffer/debounce    | Redis (`redis.asyncio`)                           |
| Mensageria         | WhatsApp Cloud API (Graph API da Meta)            |
| RAG / retrieval    | API externa HTTP (`RAG_API_URL`)                  |
| Observabilidade    | Langfuse (callback handler) + Loguru              |
| Runtime            | Python 3.13, gerenciado por `uv`                  |

---

## 2. Estrutura de diretórios

```
api_agent/
├── main.py                     # Entrypoint: configura logging e sobe o Uvicorn
├── api/
│   └── routes.py               # Endpoints FastAPI (webhook, listagem, deleção)
├── agents/
│   ├── workflow.py             # Definição do grafo LangGraph (nós + roteamento)
│   ├── nodes.py                # Funções de cada nó (secretária, especialistas, tool_node)
│   ├── tools.py                # Tools LangChain (retrieval, transferência, envio de docs)
│   ├── helpers.py              # strip_messages: saneamento/recorte do histórico
│   ├── registry.py             # AGENTS_REGISTRY (metadados p/ endpoint /agents)
│   └── prompts/                # System prompts (markdown) de cada agente
│       ├── secretaria.md
│       ├── condominial.md
│       ├── contratos.md
│       └── direito_consumidor.md
├── services/
│   ├── call_agent.py           # run_agent: compila o grafo e invoca com checkpoint
│   └── concat_messages.py      # debounce_messages: buffer de rajada via Redis
├── clients/
│   ├── whatsapp.py             # WhatsAppClient: envio de mensagens via Graph API (Meta)
│   └── retrieval.py            # retrieval_sistema / retrieval_usuario (RAG externo)
├── core/
│   └── logging.py              # setup_logging: configuração do Loguru
├── tests/                      # Testes unitários e de integração (pytest)
├── docker-compose.yml          # api + postgres + redis
├── Dockerfile                  # Imagem Python 3.13 + uv + playwright/chromium
├── pyproject.toml              # Dependências (uv)
└── .env.example                # Variáveis de ambiente necessárias
```

---

## 3. API HTTP (`api/routes.py`)

A aplicação FastAPI expõe 3 endpoints. O objeto exportado é `app`.

**Autenticação (serviço interno):** se a env `AGENTS_API_KEY` estiver setada,
`POST /messages` e `DELETE /conversations/{thread_id}` exigem o header
`Authorization: <AGENTS_API_KEY>` (valor cru, sem `Bearer`; comparação com
`secrets.compare_digest`). Falha → `403`. Com a env vazia (dev local), a
verificação é ignorada.

### 3.1 `POST /messages` — Recebimento de mensagens (contrato interno)

Ponto de entrada principal. Chamado pelo backend geral (`api`), que **já
resolveu o tenant** a partir do `phone_number_id` do webhook da Meta, validou o
estado da conversa (`agent` | `human` — em modo `human` o `api` **não** chama
este serviço) e descriptografou as credenciais do WhatsApp do tenant.

**Corpo esperado (JSON — modelo `IncomingMessage`):**

```jsonc
{
  "tenant_id": "uuid-do-escritorio",           // obrigatório
  "contact_phone_number": "5511999999999",     // obrigatório; cliente final
  "message": "texto da mensagem do cliente",   // opcional se houver attachments
  "attachments": [],                           // opcional
  "phone_number_id": "1234567890",             // obrigatório quando send_to_whatsapp=true (default)
  "access_token": "EAAG...",                   // obrigatório quando send_to_whatsapp=true (default)
  "send_to_whatsapp": true                     // opcional, default true — false pula o envio via Graph API (usado pelo playground de admin em apps/api)
}
```

**Regras de validação:**

- `message` vazio **e** sem `attachments` → `400 Mensagem inválida`.
- Campos obrigatórios ausentes → `422` (validação Pydantic).

**Fluxo interno:**

1. Monta o `thread_id = "{tenant_id}:{contact_phone_number}"` — chave de
   isolamento por tenant usada no debounce, no checkpoint do LangGraph e no
   escopo dos documentos de usuário no RAG.
2. **Debounce** (`debounce_messages`) — aguarda ~5s agrupando mensagens em
   rajada da mesma conversa. Se outra execução mais recente assumiu o buffer,
   retorna `202 Accepted` (`{"message": "Execução em andamento"}`) e encerra.
   Falha de Redis → `503`.
3. **Agente** (`run_agent`) — invoca o grafo com a mensagem consolidada.
4. **Envio** (só quando `send_to_whatsapp=true`, o default) — cada resposta
   gerada é enviada ao cliente via `WhatsAppClient.send_text_message` (Graph
   API), usando as credenciais do tenant recebidas na request. Com
   `send_to_whatsapp=false` este passo é pulado — usado pelo playground de
   admin (`apps/api`), que só quer as respostas de volta, sem canal.

**Resposta de sucesso (200):**

```json
{ "responses": ["resposta 1", "resposta 2"], "tokens_used": 1234, "current_agent": "agente_condominial" }
```

Todas as respostas geradas são devolvidas ao chamador (`worker`) para
persistência em `messages`. `tokens_used` é a soma de tokens (input+output)
das mensagens de IA da execução — incluindo as chamadas intermediárias com
tool_calls — obtida do `usage_metadata` do langchain-openai
(`sum_usage_tokens` em `services/call_agent.py`). O `worker` converte em
créditos (ceil, `CREDIT_TOKENS_PER_CREDIT`) e debita do tenant.

`current_agent` é o nome interno do agente que respondeu por último nesta
execução (`"agente_secretaria"` ou um dos 3 especialistas) — lido do estado do
grafo (`current_specialist`, `None` antes de qualquer transferência). Usado hoje
só pelo playground de admin para exibir uma tag do agente ativo na conversa.

**Erros:** `202` (execução concorrente), `400` (validação), `403` (API key),
`422` (payload), `503` (Redis), `500` (erro no agente).

> ⚠️ **Observação sobre anexos:** quando há `attachments` e `message` está vazio,
> a mensagem enviada ao agente vira `str(attachments)`.

### 3.2 `GET /agents` — Listagem de agentes/ferramentas

Retorna `AGENTS_REGISTRY` (ver `agents/registry.py`): metadados dos agentes e
suas ferramentas disponíveis. Usado para popular dashboards/Uis externas.

```json
[
  {
    "name": "Agente Condominial",
    "description": "Agente principal de atendimento condominial",
    "available": true,
    "tools": [
      { "name": "bucar_base_conhecimento_condominial", "description": "...", "available": true }
    ]
  }
]
```

### 3.3 `DELETE /conversations/{thread_id}` — Apagar conversa

Remove o histórico persistido de uma conversa no checkpointer do LangGraph
(Postgres). `thread_id` == `conversation_id`.

**Resposta:** `{ "deleted": "<thread_id>" }` · Erros → `500`.

---

## 4. Camada de serviços

### 4.1 `services/call_agent.py` — `run_agent(...)`

Compila o grafo com um checkpointer Postgres e o invoca.

```python
async def run_agent(
    message: str,
    conversation_id: str,
    attachments: list = [],
    number_whatsapp: str | None = None,
    db_uri: str = DB_URI,
    num_before_messages: int = 35,   # nº de mensagens de histórico enviadas ao LLM
    extra_data: dict = {},
) -> tuple[list[str], int, str]:
```

- Cria `config = {"configurable": {"thread_id": conversation_id}, "callbacks": [langfuse_handler]}`.
  **O `thread_id` é o `conversation_id`** — é a chave de isolamento/continuidade
  da conversa. No fluxo atual, a rota passa como `conversation_id` o valor
  composto `"{tenant_id}:{contact_phone_number}"`, garantindo isolamento por
  tenant no checkpoint e no RAG de documentos do usuário.
- `AsyncPostgresSaver.from_conn_string(db_uri)` + `await checkpointer.setup()`
  (cria tabelas se não existirem) → `graph.compile(checkpointer=...)`.
- Lê o estado anterior (`aget_state`) para saber quantas mensagens já existiam
  (`prior_count`) e assim isolar apenas as **novas** mensagens geradas nesta
  execução.
- Invoca o grafo com a `HumanMessage` nova.
- **Retorno:** tupla `(answers, tokens_used, current_agent)`:
  - `answers`: lista com os `content` de todas as `AIMessage` novas e não-vazias.
    Pode conter mais de uma mensagem (ex.: despedida de transferência + resposta
    do especialista).
  - `tokens_used`: soma de tokens (input+output) da execução.
  - `current_agent`: nome do agente ativo no estado (`"agente_secretaria"` ou um
    dos 3 especialistas, obtido de `response.get("current_specialist")`; padrão
    é `"agente_secretaria"` se indefinido).

`DB_URI` é montado como:
`postgresql://postgres:{POSTGRES_PASSWORD}@{DATABASE_HOST}:{DATABASE_PORT}/postgres`.

### 4.2 `services/concat_messages.py` — `debounce_messages(...)`

Implementa **debounce de rajada de mensagens** com Redis, para lidar com o
usuário que manda várias mensagens curtas seguidas no WhatsApp.

```python
async def debounce_messages(
    message: str,
    conversation_id: str,
    redis_host=..., redis_port=...,
    debounce_seconds: int = 5,
) -> dict
```

**Mecânica:**

1. Gera um `exec_id` (UUID) único para esta chamada.
2. Faz `RPUSH` da mensagem em `whatsapp:buffer:{conversation_id}` e grava
   `exec_id` em `whatsapp:timer:{conversation_id}` (com TTL).
3. Dorme `debounce_seconds`.
4. Ao acordar, lê `whatsapp:timer:{conversation_id}`:
   - Se o valor **não é** o próprio `exec_id`, significa que outra mensagem
     chegou depois e assumiu o processamento → retorna
     `{"combined_message": None, "other_exec_is_running": True}`.
   - Se **é** o próprio `exec_id` → drena o buffer (`LRANGE` + `DELETE`),
     concatena as mensagens com `\n` e retorna
     `{"combined_message": "...", "other_exec_is_running": False}`.

**Retorno:**

```python
{ "combined_message": str | None, "other_exec_is_running": bool }
```

> Esse padrão garante que, numa rajada, **apenas a última** chamada processa o
> lote completo; as anteriores retornam cedo com `202`.

---

## 5. O grafo de agentes (LangGraph)

### 5.1 Estado (`agents/workflow.py`)

```python
class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]  # histórico acumulado
    attachments: list
    conversation_id: str
    num_before_messages: int                             # janela de histórico
    current_specialist: str | None = None                # especialista fixado
    receptive_message_specialist: bool = False           # flag "primeira resposta"
```

- `messages` usa o reducer `operator.add` → cada nó **acrescenta** mensagens.
- `current_specialist` é a chave de roteamento: quando definido, todas as
  próximas execuções vão direto para aquele especialista.
- `receptive_message_specialist` sinaliza que é a **primeira** vez que o
  especialista assume, para que ele se apresente.

### 5.2 Nós

| Nó                          | Papel                                                        |
|-----------------------------|--------------------------------------------------------------|
| `agente_secretaria`         | Triagem inicial. Tools: transfer + RAG do escritório (KB do tenant). |
| `agente_condominial`        | Especialista em direito condominial. Tools: transfer + RAG condominial + RAG usuário + RAG do escritório. |
| `agente_contratos`          | Especialista em contratos. Tools: transfer + RAG contratos + RAG usuário + RAG do escritório. |
| `agente_direito_consumidor` | Especialista em direito do consumidor. Tools: transfer + RAG consumidor + RAG usuário + RAG do escritório. |
| `tool_node`                 | Executa as tool calls e atualiza o estado.                   |

Cada nó:
1. Recorta o histórico com `strip_messages(state["messages"], num_before_messages)`.
2. Carrega o system prompt do arquivo `.md` correspondente.
3. Faz `model.bind_tools([...])` e `ainvoke`.
4. Se o modelo chamou tools → `Command(goto="tool_node")`; senão →
   `Command(goto=END)`.

**Injeção de "primeira resposta"** (especialistas): quando
`receptive_message_specialist` é `True`, o prompt recebe uma instrução extra
pedindo que o agente se apresente, informe a especialidade e assuma o
atendimento; depois a flag é zerada.

**Despedida de transferência** (secretária e condominial): quando o modelo chama
`transfer_to_specialist` sem texto próprio, injeta-se uma `AIMessage` do tipo
_"um momento... vou te passar pro especialista de X agora."_ para o cliente ter
feedback antes da troca.

### 5.3 Roteamento

```python
# A partir de START
def route_from_start(state):
    return state.get("current_specialist") or "agente_secretaria"

# Após o tool_node
def route_from_tool_node(state):
    return state.get("current_specialist") or "agente_secretaria"
```

- **Sem especialista fixado** → começa na secretária.
- **Com especialista fixado** → vai direto para ele (persistido no checkpoint).
- Após o `tool_node`, volta para o agente atual (que agora vê o resultado da
  tool no histórico).

**Ciclo típico de transferência:**

```
START → agente_secretaria → (transfer_to_specialist) → tool_node
      → (current_specialist agora setado) → agente_condominial → END
```

### 5.4 `tool_node` (`agents/nodes.py`)

- Lê `tool_calls` da última mensagem.
- Para cada call, resolve a tool pelo nome (`tools` de `agents/tools.py`) e faz
  `await tool.ainvoke(args)`.
- **Injeção de `conversation_id` do estado (segurança multi-tenant):** para as
  tools em `STATE_SCOPED_TOOLS` (`bucar_base_conhecimento_usuario`,
  `buscar_base_conhecimento_escritorio`), o `conversation_id` recebido do LLM
  em `tool_call["args"]` é **sempre sobrescrito** por `state["conversation_id"]`
  antes do `ainvoke`. O LLM nunca decide o `conversation_id` real dessas
  buscas — evita que uma mensagem maliciosa induza a tool a vazar dado de
  outro tenant.
- Se a tool retorna um `Command` (caso de `transfer_to_specialist`), aplica o
  `update` ao estado (ex.: seta `current_specialist`) e emite uma `ToolMessage`
  vazia.
- Caso contrário, emite uma `ToolMessage` com o resultado stringificado.

### 5.5 `strip_messages` (`agents/helpers.py`)

Sanitiza e recorta o histórico antes de mandar ao LLM. Responsabilidades:

- Reconstrói mensagens limpas por tipo (human/ai/system/tool).
- **Fecha tool calls pendentes**: para cada `AIMessage` com `tool_calls` que não
  tenha a `ToolMessage` correspondente, injeta um placeholder vazio — evita erro
  da OpenAI de "tool_call sem resposta".
- Recorta às últimas `num_before_messages` mensagens, **sem cortar no meio de um
  bloco de tool** (retrocede até uma fronteira segura).

---

## 6. Ferramentas (`agents/tools.py`)

| Tool                                        | Tipo   | Função                                                                 |
|---------------------------------------------|--------|------------------------------------------------------------------------|
| `transfer_to_specialist(current_specialist)`| sync   | Retorna `Command` que seta `current_specialist` e `receptive_message_specialist=True`. Valores válidos: `agente_condominial`, `agente_contratos`, `agente_direito_consumidor`. |
| `bucar_base_conhecimento_condominial(query)`| async  | RAG na base do sistema, categoria `condominial`.                       |
| `bucar_base_conhecimento_contratos(query)`  | async  | RAG na base do sistema, categoria `contratos`.                         |
| `bucar_base_conhecimento_direito_consumidor(query)` | async | RAG na base do sistema, categoria `direito_consumidor`.          |
| `bucar_base_conhecimento_usuario(query, conversation_id)` | async | RAG na base de documentos privados do usuário.             |
| `buscar_base_conhecimento_escritorio(query, conversation_id)` | async | RAG na base de conhecimento própria do escritório (tenant), via `/retrieval/users` com `conversation_id="kb"`. |
| `enviar_documento(url, conversation_id)`    | sync   | Baixa um documento de uma URL e faz upload para endpoint de inserção.  |

A lista `tools` exportada (usada pelo `tool_node`) contém as 5 tools de
retrieval + `transfer_to_specialist`.

Para `bucar_base_conhecimento_usuario` e `buscar_base_conhecimento_escritorio`,
o `conversation_id` declarado na assinatura da tool existe só para o LLM
"preencher" a chamada — o `tool_node` **sempre** substitui esse valor pelo
`state["conversation_id"]` real antes de invocar (ver §5.4, `STATE_SCOPED_TOOLS`
em `agents/nodes.py`). Isso é o que garante isolamento de tenant: o LLM nunca
controla de fato qual tenant/conversa é consultado nessas duas tools.

> ⚠️ **Pontos de atenção para integração:**
> - `enviar_documento` **não está na lista `tools`** nem é bindada aos nós no
>   código atual (os prompts mencionam ferramentas de documento como
>   `enviar_arquivo`, `fazer_contrato` etc., mas elas **não estão implementadas**
>   como tools reais). Trata-se de funcionalidade parcial/em desenvolvimento.
> - `ENDPOINT_URL`, `API_KEY` e `CONVERSATION_ID` em `tools.py` estão
>   **hardcoded** (`http://localhost:8000/...`, chave placeholder). Devem ser
>   parametrizados via env antes de uso em produção.
> - Há **typos intencionais** mantidos por compatibilidade: `bucar_...` (função)
>   e `convesation_id` (campo do form no upload).

### RAG externo (`clients/retrieval.py`)

Ambas as funções fazem `POST` para uma API externa e retornam a lista
`data["results"]` (ou `[]` em caso de erro — falha degradada, não levanta
exceção):

| Função               | Endpoint                          | Payload                                        |
|----------------------|-----------------------------------|------------------------------------------------|
| `retrieval_sistema`  | `POST {RAG_API_URL}/retrieval/system` | `{"base": <categoria>, "message": <query>}` |
| `retrieval_usuario`  | `POST {RAG_API_URL}/retrieval/users`  | `{"tenant_id": <tenant>, "conversation_id": <contato>, "message": <query>}` |
| `retrieval_escritorio` | `POST {RAG_API_URL}/retrieval/users` | `{"tenant_id": <tenant>, "conversation_id": "kb", "message": <query>}` |

`retrieval_usuario` recebe o `thread_id` composto (`"{tenant_id}:{contact_phone_number}"`)
e o divide no primeiro `:` para enviar `tenant_id` e `conversation_id` separados —
contrato multi-tenant do `api_rag` (ver `apps/api_rag/API.md` §3.7).

`retrieval_escritorio` também recebe o `thread_id` composto, mas usa só a
parte do `tenant_id` (via `partition(":")`) — a busca é sempre feita com o
`conversation_id` reservado `"kb"` (constante `KB_CONVERSATION_ID`), que é o
marcador usado pela ingestão da base de conhecimento do escritório (worker do
monorepo, fora deste microserviço).

Header: `Authorization: {RAG_API_KEY}`. Timeout: 30s.

---

## 7. Cliente WhatsApp (`clients/whatsapp.py`)

`WhatsAppClient` — cliente `httpx.AsyncClient` para enviar mensagens via
**WhatsApp Cloud API** (Graph API da Meta). As credenciais são **por tenant** e
chegam no construtor (vindas da request): o serviço não armazena token de
nenhum tenant.

- Suporta uso como async context manager
  (`async with WhatsAppClient(phone_number_id, access_token) as c:`) ou
  instância simples (cria o client sob demanda em `_get_client`).
- `_safe_request` centraliza tratamento de erro (timeout, conexão, HTTP) e
  **nunca levanta** — retorna `{"success", "data", "error"}`.
- **Métodos principais:**

```python
await client.send_text_message(to, text)
# POST {GRAPH_API_BASE_URL}/{GRAPH_API_VERSION}/{phone_number_id}/messages
# payload: {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
# header:  {"Authorization": "Bearer <access_token>"}

await client.send_document_message(to, link, filename=None, caption=None)
# type "document" com link — para o agente enviar PDFs/documentos gerados.
```

---

## 8. Configuração — variáveis de ambiente

Todas carregadas via `python-dotenv` (`.env`). Ver `.env.example`:

```dotenv
# RAG / Retrieval
RAG_API_URL=
RAG_API_KEY=

# Redis (debounce / buffer de mensagens)
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=

# PostgreSQL (checkpoint do LangGraph)
POSTGRES_PASSWORD=
DATABASE_HOST=postgres
DATABASE_PORT=5432

# OpenAI (LLM dos agentes)
OPENAI_API_KEY=

# Auth interna (chamadas do `api` → este serviço)
AGENTS_API_KEY=

# WhatsApp Cloud API (credenciais por tenant chegam em cada request)
GRAPH_API_BASE_URL=https://graph.facebook.com
GRAPH_API_VERSION=v23.0

# Langfuse (observabilidade — opcional)
LANGFUSE_SECRET_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_BASE_URL=

# Logging
LOG_LEVEL=INFO          # default INFO
LOG_FILE=logs/api.log   # se setado, ativa arquivo com rotação 10MB / retenção 7d
```

| Variável | Onde é usada | Obrigatória |
|----------|--------------|-------------|
| `OPENAI_API_KEY` | `ChatOpenAI` (nodes.py) | Sim |
| `POSTGRES_PASSWORD`, `DATABASE_HOST`, `DATABASE_PORT` | `DB_URI` (call_agent.py) | Sim |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` | debounce (concat_messages.py) | Sim (para `POST /messages`) |
| `RAG_API_URL`, `RAG_API_KEY` | retrieval (clients/retrieval.py) | Sim (para tools de conhecimento) |
| `AGENTS_API_KEY` | auth interna (api/routes.py) | Não (sem ela, auth desligada — só dev) |
| `GRAPH_API_BASE_URL`, `GRAPH_API_VERSION` | envio via Graph API (clients/whatsapp.py) | Não (defaults: `https://graph.facebook.com` / `v23.0`) |
| `LANGFUSE_*` | callback de tracing | Não (degradação) |
| `LOG_LEVEL`, `LOG_FILE` | logging | Não |

---

## 9. Execução

### Local (uv)

```bash
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 8082
# main.py também roda com reload=True se executado diretamente: uv run python main.py
```

### Docker Compose

Sobe `api` (porta 8082) + `postgres:17` (5432) + `redis:8` (6379), com
healthchecks e volumes persistentes:

```bash
docker compose up --build
```

> O `Dockerfile` instala `playwright + chromium` (dependência ainda não usada no
> código de agente atual — provavelmente reservada para geração/render de
> documentos).

### Testes

```bash
uv run pytest            # configurado em pyproject.toml (asyncio_mode=auto, testpaths=tests)
```

`test_agents.py` (raiz) é um **script manual** de invocação do grafo contra um
Postgres real (não é teste automatizado do pytest).

---

## 10. Como integrar em outro projeto

Dependendo do nível de acoplamento desejado:

### Opção A — Consumir via HTTP (menor acoplamento, recomendado)

Trate este serviço como um microsserviço interno. O projeto integrador (`api`):

1. Recebe o webhook da Meta, resolve `tenant_id` + credenciais e chama
   `POST /messages` com o payload da seção 3.1 (header
   `Authorization: <AGENTS_API_KEY>`).
2. Opcionalmente consome `GET /agents` para exibir agentes/ferramentas.
3. Usa `DELETE /conversations/{thread_id}` para resetar conversas.

O serviço cuida sozinho de debounce, estado e envio das respostas via Graph
API; o integrador persiste as respostas retornadas e contabiliza créditos.

### Opção B — Importar o grafo como biblioteca (maior acoplamento)

Para embutir a lógica de agentes sem o servidor HTTP:

```python
from services.call_agent import run_agent

respostas: list[str] = await run_agent(
    message="texto do usuário",
    conversation_id="id-unico-da-conversa",   # vira o thread_id do checkpoint
    attachments=[],
    num_before_messages=35,
)
```

Requisitos mínimos para a Opção B:
- Postgres acessível (`DB_URI`) — o checkpoint é obrigatório; `run_agent`
  sempre compila o grafo com um `AsyncPostgresSaver`.
- `OPENAI_API_KEY` e, se as tools de conhecimento forem usadas,
  `RAG_API_URL`/`RAG_API_KEY`.
- Redis e WhatsApp **não** são necessários nesta opção (só a rota `POST /messages` os usa).

### Pontos de contrato importantes

- **`thread_id` = `"{tenant_id}:{contact_phone_number}"`**: é a chave de
  continuidade e de isolamento por tenant da conversa (checkpoint, debounce e
  RAG de documentos do usuário). O integrador deve garantir estabilidade desses
  dois valores por cliente.
- **Estado persistente**: as conversas ficam no Postgres indefinidamente até um
  `DELETE`. Planeje uma política de retenção/limpeza.
- **Respostas múltiplas**: `run_agent` retorna uma **lista** de strings; envie
  todas ao cliente na ordem.
- **Prompts são arquivos**: editar comportamento dos agentes = editar os `.md` em
  `agents/prompts/`. Os caminhos são relativos ao diretório de execução
  (`agents/prompts/...`), então **rode a aplicação a partir da raiz do projeto**.

---

## 11. Débitos técnicos / atenção (para o integrador)

- `agents/tools.py`: `ENDPOINT_URL`, `API_KEY`, `CONVERSATION_ID` **hardcoded**;
  parametrizar via env.
- Tools de geração de documento citadas nos prompts (`fazer_contrato`,
  `enviar_arquivo`, `fazer_multa`, etc.) **não estão implementadas** — só o
  `enviar_documento` existe, e ele não está bindado a nenhum agente.
- Nomes com typo preservados por compatibilidade: `bucar_base_conhecimento_*`,
  `convesation_id`.
- A despedida de transferência automática só está implementada em
  `agente_secretaria` e `agente_condominial` (não em contratos/consumidor).
- `run_agent` usa argumentos default mutáveis (`attachments=[]`,
  `extra_data={}`) — não os mutar dentro da função.
- `main.py` sobe com `reload=True` (modo desenvolvimento). Em produção use o
  comando do Dockerfile/compose.

---

## 12. Referência rápida de endpoints

| Método | Rota                            | Descrição                                    | Sucesso | Erros                |
|--------|---------------------------------|----------------------------------------------|---------|----------------------|
| POST   | `/messages`                     | Mensagem do cliente (contrato interno, via `api`) | 200 | 202/400/403/422/503/500 |
| GET    | `/agents`                       | Lista agentes e ferramentas                  | 200     | —                    |
| DELETE | `/conversations/{thread_id}`    | Apaga histórico da conversa                  | 200     | 403/500              |
