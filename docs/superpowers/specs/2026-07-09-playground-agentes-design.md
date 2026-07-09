# Design — Playground de Agentes (`/admin/playground`)

Data: 2026-07-09
Status: aprovado

## Objetivo

Chat de teste direto na plataforma para os desenvolvedores (time da Advoxs) conversarem com os agentes sem passar pelo WhatsApp — iterar em prompts/tools/comportamento com feedback imediato. Ferramenta interna de dev, dentro do painel `/admin` (autenticação de `platform_admin` já existente).

## Decisões de produto

- **Playground no `/admin`**, não um tenant sandbox com fluxo real completo: iteração síncrona e rápida (sem fila/worker/polling), sem poluir `conversations`/`messages`/créditos. A ideia de "tenant só pra testes" é coberta pelo **seletor de tenant** — o dev escolhe qual tenant simular (um real, pra testar contra a KB dele, ou um de teste criado via seed/cadastro).
- **Efêmero por design**: nada é persistido no Postgres do `api`. A memória da conversa vive só no checkpoint do LangGraph (`thread_id` composto) e no estado do chat no browser.
- **Sem débito de créditos**: execuções do playground não geram `credit_transactions` nem tocam `tenants.credit_balance`.
- **Debounce mantido**: o comportamento do agente fica idêntico ao real (respostas demoram ~5s a mais; testar sem debounce esconderia bugs de agrupamento).
- **Tag do agente ativo**: cada conversa mostra qual agente está atendendo (secretária ou o especialista fixado), atualizada a cada resposta — o dev vê a triagem/transferência acontecendo.
- **Só texto** nesta entrega (anexos/mídia ficam de fora).

## Mudanças no `agents`

1. `IncomingMessage` ganha `send_to_whatsapp: bool = True`. Quando `false`, o bloco do `WhatsAppClient` em `POST /messages` é pulado — debounce, grafo, tools de RAG, checkpoint e contagem de tokens rodam idênticos. Default `true` preserva o contrato atual com o `worker` sem tocá-lo.
2. `phone_number_id` e `access_token` passam a ser opcionais (`""` por default) — só são usados no envio, que não acontece no playground. (O `worker` continua enviando ambos; a validação de "obrigatório quando `send_to_whatsapp=true`" não é necessária — a Graph API falha sozinha com credencial vazia, mesmo comportamento de hoje com credencial inválida.)
3. `run_agent` passa a devolver também o agente ativo, lido do estado final do grafo (`State.current_specialist`; `None` → secretária): retorno vira `(responses, tokens_used, current_agent)`, onde `current_agent` é `"agente_secretaria" | "agente_condominial" | "agente_contratos" | "agente_direito_consumidor"`.
4. Resposta de `POST /messages` ganha o campo: `{"responses": [...], "tokens_used": N, "current_agent": "..."}`. O `worker` ignora campos extras (lê só `responses`/`tokens_used`) — sem breaking change.
5. `API_AGENTS.md` atualizado (contrato de request/response).

## Mudanças no `api`

Rotas novas em `app/api/v1/platform_admin/playground.py`, autenticadas com `get_current_platform_admin` (mesmo isolamento do resto do admin):

- **`POST /api/v1/platform-admin/playground/messages`** — body `{tenant_id: uuid, session_id: str, message: str}` (message non-empty). Valida que o tenant existe (404 se não), chama o `agents` com:
  - `contact_phone_number = "playground-{session_id}"` (→ `thread_id = "{tenant_id}:playground-{session_id}"`, isolado de contatos reais pelo prefixo)
  - `send_to_whatsapp = false`, `phone_number_id`/`access_token` vazios
  - timeout largo (120s — execução de agente é lenta)
  - Resposta 200 do agents → `{responses: [...], tokens_used: N, current_agent: "...", grouped: false}`
  - Resposta 202 do agents (debounce agrupou numa execução em andamento) → `{responses: [], tokens_used: null, current_agent: null, grouped: true}`
  - Erro/timeout do agents → 502 com mensagem genérica (sem vazar erro interno).
- **`DELETE /api/v1/platform-admin/playground/conversations/{tenant_id}/{session_id}`** — repassa pro `DELETE /conversations/{thread_id}` do `agents`. Higiene do checkpoint; o botão "Nova conversa" do front também gera `session_id` novo, então a UX não depende deste delete funcionar.
- Client do agents no `api`: módulo novo `app/clients/agents.py` (httpx async, header `Authorization: AGENTS_API_KEY`, base `AGENTS_SERVICE_URL` — env já existe como `agents_service_url` no config).
- Nenhuma persistência no Postgres. Allowlist do proxy admin (`platform-admin/*`) já cobre as rotas novas — zero mudança no proxy.

## Frontend (`/admin/playground`)

- Nav lateral do admin ganha o item **Playground** (Dashboard / Tenants / Playground / Sair) — nas 4 páginas do admin.
- Topo: **seletor de tenant** (dropdown alimentado por `GET platform-admin/tenants`, já existente) + **tag do agente ativo** + botão **"Nova conversa"**.
- **Tag do agente ativo**: badge visível na conversa (ex: "Secretária", "Condominial", "Contratos", "Direito do Consumidor" — labels amigáveis mapeadas dos nomes internos), atualizada com o `current_agent` de cada resposta. Antes da primeira resposta: "Secretária" (estado inicial de toda conversa).
- Chat estilo mensageria (mesma linguagem visual da thread de `/conversas`): mensagens do dev à direita, respostas do agente à esquerda, indicador "agente digitando..." enquanto a request está em voo, `tokens_used` exibido discretamente em cada resposta.
- `session_id` gerado no client (`crypto.randomUUID()`) ao abrir a página ou clicar "Nova conversa"; trocar de tenant também reseta a sessão (novo `session_id`, chat limpo, tag volta pra "Secretária").
- Componente principal: `AdminPlaygroundPanel.tsx` (client component, via `adminBackendFetch`).

## Erros e casos-limite

| Caso | Comportamento |
|---|---|
| Agente demora > 120s | 502 do api → erro inline no chat ("O agente falhou ao responder — veja os logs do serviço"), histórico da tela preservado |
| Debounce agrupa (202 → `grouped: true`) | Aviso inline "mensagem agrupada à execução em andamento" — as respostas chegam na execução que já roda |
| Tenant sem arquivos na KB | Normal — tool de KB do escritório retorna vazio, agente responde sem esse contexto |
| `tenant_id` inexistente | 404 |
| Mensagem vazia | 422 (validação Pydantic) |
| Dois devs no mesmo tenant | Sessões independentes (`session_id` distinto → `thread_id` distinto) |
| Sessão de tenant (não admin) tenta acessar | 401 (isolamento de sessão já existente) |

## Testes

- **agents**: `send_to_whatsapp=false` não instancia `WhatsAppClient` (mock) e ainda retorna respostas/tokens/`current_agent`; default `true` mantém o envio (regressão do contrato com o worker); `current_agent` reflete o estado (None → `agente_secretaria`).
- **api**: rota exige platform_admin (401 sem token); happy path com mock do client agents (200 → passthrough de responses/tokens/current_agent); 202 → `grouped: true`; 404 de tenant; erro do agents → 502 sem vazar detalhe; nada gravado no banco.
- **web**: chat renderiza mensagens enviadas/recebidas; tag do agente atualiza com `current_agent` da resposta; estado "digitando"; erro inline; "Nova conversa"/troca de tenant resetam chat e tag.

## Fora de escopo desta entrega

- Anexos/mídia no playground.
- Visualizar o trace interno do grafo (quais tools foram chamadas, chunks de RAG retornados) — o dev usa o Langfuse pra isso; candidato a evolução futura.
- Playground para usuários de tenant (é ferramenta interna de dev).
- Streaming das respostas (a resposta chega completa ao fim da execução).
