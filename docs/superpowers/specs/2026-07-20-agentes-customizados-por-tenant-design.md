# Agentes customizados por tenant — Design

## Contexto

Hoje os agentes de IA da plataforma são **fixos e iguais para todo tenant**: uma secretária de triagem (`agente_secretaria`) transfere a conversa pra um de 3 especialistas hardcoded (condominial, contratos, direito do consumidor). Cada um é uma função Python própria em `apps/agents/agents/nodes.py`, com seu próprio arquivo de prompt (`apps/agents/agents/prompts/*.md`) e sua própria lista fixa de tools bindada no código. O grafo do LangGraph (`apps/agents/agents/workflow.py`) é **compilado com esses 4 nós nomeados literalmente** — as edges condicionais (`route_from_start`/`route_from_tool_node`) enumeram os 4 nomes.

A mudança pedida: cada tenant passa a **criar os próprios agentes** (nome, instruções, um agente marcado como ponto de entrada), como no Chatwoot. Cada agente pode ter bases de conhecimento anexadas e só busca dentro delas — nunca na base inteira do escritório, nunca na de outro agente.

## Decisões de produto (já tomadas)

1. **Roteamento**: um agente é marcado como **ponto de entrada** por tenant (recebe a primeira mensagem de cada conversa nova). Qualquer agente pode transferir a conversa pra outro via uma tool genérica — generaliza o padrão atual (secretária + `transfer_to_specialist`) pra N agentes quaisquer, sem hierarquia fixa entre eles.
2. **Migração dos 3 especialistas fixos**: para todo tenant já existente, os 4 agentes atuais (secretária + 3 especialistas) são **clonados como linhas próprias na tabela `agents`** — prompt atual copiado pro campo `instructions`, secretária marcada como ponto de entrada. Zero mudança de comportamento até a Etapa 2 trocar o motor; a partir daí o tenant pode editar/apagar essas linhas como qualquer agente seu.
3. **Compartilhamento de base de conhecimento entre agentes**: uma base **pode** ser anexada a mais de um agente (modelo muitos-pra-muitos, sem duplicar ingestão) — mas o **padrão da UX** é direcionado: o upload em `/base-de-conhecimento` já pede "pra qual agente é este arquivo" e cria esse vínculo direto; anexar o mesmo arquivo (já existente) a outro agente depois é uma ação explícita separada, não automática.

## Abordagem escolhida — 3 etapas sequenciais

Cada etapa entrega software testável e funcionando por si só, seguindo o padrão já usado neste repo pra iniciativas grandes (ex: wallet unificada de créditos).

---

### Etapa 1 — Modelo de dados + CRUD no `api`

**Sem tocar `apps/agents` nesta etapa** — o motor de execução continua lendo os arquivos `.md` fixos, exatamente como hoje. Etapa 1 só entrega a gestão de dados.

#### Modelo de dados (migration Alembic, tenant-scoped, RLS igual às demais tabelas)

**`agents`**
- `id` (uuid, PK)
- `tenant_id` (FK → `tenants`)
- `name` (string, obrigatório)
- `instructions` (text, obrigatório — substitui os arquivos `.md` fixos)
- `is_entry_point` (bool, default `false`) — recebe a primeira mensagem de conversas novas. Índice único parcial `(tenant_id) WHERE is_entry_point = true` garante exatamente 1 por tenant.
- `created_at`, `updated_at`

**`agent_knowledge_base_files`** (tabela de junção, muitos-pra-muitos)
- `agent_id` (FK → `agents`, ON DELETE CASCADE)
- `knowledge_base_file_id` (FK → `knowledge_base_files`, ON DELETE CASCADE)
- `created_at`
- PK composta `(agent_id, knowledge_base_file_id)`

#### Migração de dados (dentro da mesma migration ou num script de backfill, a decidir na fase de plano)

Para cada tenant existente: insere 4 linhas em `agents` —
- "Secretária" (`instructions` = conteúdo atual de `secretaria.md`, `is_entry_point=true`)
- "Condominial" (`instructions` = conteúdo atual de `condominial.md`)
- "Contratos" (`instructions` = conteúdo atual de `contratos.md`)
- "Direito do Consumidor" (`instructions` = conteúdo atual de `direito_consumidor.md`)

Nenhum `knowledge_base_file` existente é auto-vinculado a essas linhas — hoje esses 4 agentes buscam a base do escritório inteira via `buscar_base_conhecimento_escritorio`; a partir da Etapa 2 eles passam a só ver o que for explicitamente anexado. Fora de escopo desta migração decidir qual base vai pra qual — cabe ao tenant reanexar depois, manualmente, se quiser preservar o comportamento de busca ampla.

#### Endpoints novos (`/api/v1/agents`, autenticados, tenant-scoped)

- `GET /agents` — lista os agentes do tenant.
- `POST /agents` — cria (`name`, `instructions`, `is_entry_point` opcional — se `true`, desmarca o anterior na mesma transação).
- `PATCH /agents/{id}` — edita `name`/`instructions`/`is_entry_point` (mesma regra de exclusividade).
- `DELETE /agents/{id}` — apaga; recusa com 409 se for o ponto de entrada atual (precisa promover outro antes) ou se for o único agente do tenant (sempre precisa haver ao menos 1).
- `POST /agents/{id}/knowledge-base-files` — anexa um `knowledge_base_file_id` já existente (deve pertencer ao mesmo tenant).
- `DELETE /agents/{id}/knowledge-base-files/{file_id}` — desanexa.
- `POST /knowledge-base/files` (rota já existente) ganha um campo obrigatório novo no form: `agent_id` — o upload já cria o vínculo em `agent_knowledge_base_files` na mesma transação que cria o `knowledge_base_files`, refletindo o padrão da UX (upload é sempre "pra um agente").

## Etapa 2 — Motor dinâmico no `agents` service

A virada arquitetural real. O grafo do LangGraph deixa de ter um nó por agente e passa a ter só **dois nós, sempre**: `agent_node` (genérico) e `tool_node`. Isso nunca precisa ser recompilado quando um tenant cria um agente novo.

#### Estado do grafo
- `current_specialist: str | None` → renomeado pra `current_agent_id: str | None`.
- Campo novo: `agents: list[dict]` — a lista completa dos agentes do tenant (id, name, instructions, is_entry_point, knowledge_base_file_ids), resolvida pelo chamador a cada request.

#### Contrato de `POST /messages`
Ganha o campo `agents: list[dict]`, resolvido pelo `worker`/`api` (que já tem acesso ao Postgres principal) a cada chamada — **mesmo padrão que `end_customer_billing` já usa hoje**. O `agents` service nunca ganha acesso direto ao banco `advoxs`; continua só com o seu próprio banco de checkpoint (`advoxs_agents`).

#### `agent_node` (substitui os 4 nós fixos)
Em runtime: resolve qual entrada de `state["agents"]` corresponde a `state["current_agent_id"]` (ou ao agente marcado `is_entry_point` se `current_agent_id` for `None` — primeira mensagem da conversa). Monta a chamada ao LLM com `SystemMessage(content=agente["instructions"])` e `model.bind_tools([transfer_to_agent, buscar_base_conhecimento_agente, ...])` — o conjunto de tools genéricas disponível é o mesmo pra todo agente (nenhuma tool específica por agente nesta entrega, ver "Fora de escopo").

#### Tools novas/generalizadas
- **`transfer_to_agent(agent_id: str)`** substitui `transfer_to_specialist(current_specialist: Literal[...])`. `tool_node` valida que o `agent_id` recebido do LLM está de fato em `state["agents"]` daquele tenant antes de aceitar a transferência — nunca confia no valor cru (mesmo princípio do `STATE_SCOPED_TOOLS`/`BILLING_GATED_TOOLS` de hoje). ID inválido/inexistente → a tool devolve um erro pro LLM tentar de novo, sem mudar o estado.
- **`buscar_base_conhecimento_agente(query: str, conversation_id: str)`** substitui as 3 tools fixas (`bucar_base_conhecimento_condominial/contratos/direito_consumidor`) e a tool de escritório (`buscar_base_conhecimento_escritorio`) — vira a ÚNICA tool de busca de KB "own" do tenant. `tool_node` injeta, além do `conversation_id` (padrão já existente), a lista `knowledge_base_file_ids` do agente ATUAL (de `state["agents"]`, nunca do LLM). Chama `api_rag` passando esses IDs.
- `bucar_base_conhecimento_usuario` (base pessoal da conversa, upload ad-hoc pelo contato) não muda — não é afetada por essa feature.
- `gerar_link_pagamento_cliente` e o gate de billing (`is_billing_blocked`) não mudam de comportamento — só passam a ser reavaliados dentro de `agent_node` (genérico) em vez de replicados nos 3 nós fixos.

#### `api_rag` — filtro novo, sem mudar schema
`POST /retrieval/users` ganha um parâmetro opcional `doc_ids: list[str]`. Quando presente, o filtro do Qdrant adiciona `doc_id IN (...)` além do `tenant_id` já obrigatório — nenhuma mudança de payload/schema, nenhuma re-ingestão necessária. Sem `doc_ids`, o comportamento atual (todo o pool do tenant, via `conversation_id="kb"`) continua existindo pra quem ainda usa o caminho antigo durante a transição.

#### Limpeza que essa etapa naturalmente faz
`apps/agents/agents/prompts/*.md`, os 4 nós fixos em `nodes.py`, e as tools fixas de KB por categoria (`bucar_base_conhecimento_condominial/contratos/direito_consumidor`) somem — substituídos pelo `agent_node` genérico e pela tool única de KB. `registry.py` (hoje um `AGENTS_REGISTRY` hardcoded e defasado, usado só por `GET /agents` do próprio `agents` service — não confundir com a tabela nova `agents` do `api`) também é revisado nessa etapa: passa a refletir tools genéricas disponíveis, não mais uma lista fixa de "agentes" da plataforma.

## Etapa 3 — Frontend (`web`)

- **`/agentes`**: lista os agentes do tenant (nome, badge "ponto de entrada" no que for), criar novo, editar (nome/instruções/marcar como ponto de entrada — troca o anterior automaticamente), apagar (com a mesma validação de 409 do backend refletida na UI).
- **Dentro da tela de cada agente**: lista das bases anexadas + botão pra anexar um arquivo já existente na base do escritório (busca/seleciona entre os já enviados) + atalho pra upload direto já pré-selecionando aquele agente como destino.
- **`/base-de-conhecimento`**: formulário de upload ganha o campo obrigatório de agente-destino (dropdown com os agentes do tenant).

## Fora de escopo (nesta entrega)

- Editor visual de fluxo/multi-passo por agente — só instrução em texto livre, como hoje.
- Limite de quantos agentes um tenant pode criar, ou cobrança adicional por agente.
- Tools específicas por agente (ex: um agente ter uma tool de geração de documento que outro não tem) — todo agente usa o mesmo conjunto de tools genéricas (transferência, busca de KB própria, busca de KB pessoal do contato, geração de link de pagamento quando aplicável).
- Migrar o playground de admin (`/admin/playground`) pra usar agentes dinâmicos — ele continua chamando o mesmo `POST /messages`; se a Etapa 2 mudar o contrato, o playground precisa passar a resolver e enviar `agents` também, mas o design da tela do playground em si não muda.
- Decidir automaticamente qual base de conhecimento antiga (pré-migração) vai para qual agente clonado — fica para o tenant reanexar manualmente se quiser.

## Testes

- **Etapa 1**: testes de unidade do CRUD (`/api/v1/agents/*`), da constraint de único ponto de entrada, da recusa de exclusão do ponto de entrada/único agente, do vínculo criado junto no upload. Migration testada com dado real de tenant seed.
- **Etapa 2**: testes de unidade do `agent_node` genérico (troca de agente via `current_agent_id`, primeira mensagem cai no ponto de entrada), da validação de `agent_id` em `transfer_to_agent` (rejeita ID de outro tenant/inexistente), da injeção de `knowledge_base_file_ids` em `buscar_base_conhecimento_agente` (nunca confia no valor do LLM). Teste de integração real contra a LLM (mesmo padrão já usado nas mudanças de prompt anteriores) confirmando transferência entre 2 agentes fictícios.
- **Etapa 3**: testes de componente das telas novas, espelhando o padrão já usado em `/base-de-conhecimento` e `/configuracoes/cobranca-clientes`.
