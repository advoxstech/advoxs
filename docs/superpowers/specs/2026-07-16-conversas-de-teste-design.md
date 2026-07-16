# Conversas de teste (aba de testes do tenant)

**Data:** 2026-07-16
**Status:** aprovado

## Problema

O escritório só consegue experimentar os agentes com um número de WhatsApp conectado (setup na
Meta, webhook etc.). Não há como testar o atendimento — nem durante o onboarding, nem depois
(ex: validar a base de conhecimento recém-subida). O playground do admin (`/admin/playground`)
resolve isso só pra plataforma, é efêmero por design e não é acessível a tenants.

## Solução

Aba "Testes" em `/conversas`: o usuário do tenant conversa com os próprios agentes simulando o
cliente final, sem WhatsApp — e as conversas ficam **persistidas** (diferente do playground).

### 1. Modelo de dados

- Migration `0011`: `conversations.is_test` (boolean, `NOT NULL DEFAULT false`).
- Conversa de teste usa `contact_phone_number` sintético **`teste-{uuid.hex[:12]}`** — satisfaz
  o UNIQUE `(tenant_id, contact_phone_number)`, isola o checkpoint do LangGraph
  (`thread_id = "{tenant_id}:teste-..."`) e nunca colide com payload de webhook (a Meta só
  envia números).
- Espelhar a coluna em `apps/worker/app/tables.py` (o worker mantém o schema em sincronia —
  aprendizado da feature anterior), mesmo sem o worker usá-la.

### 2. API (rotas tenant, autenticadas via `get_current_tenant` + `get_tenant_session`)

- **`POST /api/v1/test-conversations`** → cria `Conversation(is_test=true, state=agent,
  contact_phone_number="teste-{hex12}")`, retorna `ConversationOut` (que ganha o campo
  `is_test`). 201.
- **`POST /api/v1/conversations/{id}/test-messages`** (body `{content: str, min 1}`):
  1. 404 se a conversa não é do tenant; **409 se `is_test=false`**.
  2. **402 se `tenant.credit_balance <= 0`** (mesmo padrão da rota de resumo).
  3. Persiste a mensagem do usuário como `sender_type="contact"` (commit antes de chamar o
     agents — se o agents falhar, a mensagem do usuário sobrevive no histórico).
  4. Chama o `agents` **direto e síncrono** via `POST /messages` com `send_to_whatsapp=false`
     (reaproveita `send_playground_message` do client, que já faz exatamente essa chamada).
     202/debounce (`result is None`) → responde `{grouped: true}` sem persistir resposta.
  5. Persiste as respostas (`sender_type="agent"`, `delivery_status=NULL` — não houve envio),
     grava `tokens_used`/`credits_consumed` na primeira, lança `credit_transactions`
     (`consumption`, `related_message_id` = primeira resposta) e atualiza
     `tenants.credit_balance` — mesma fórmula do worker: `ceil(tokens /
     CREDIT_TOKENS_PER_CREDIT)` (env já replicada no `api`), tudo na mesma transação.
  6. Retorna `{messages: [MessageOut...], grouped: bool}` — a mensagem do contato + as
     respostas do agente.
  - Erro do agents (rede/5xx) → 502 com detail genérico; a mensagem do contato já persistida
    permanece.
- **`DELETE /api/v1/conversations/{id}`** → **só** conversas de teste (`is_test=false` → 409).
  Apaga `messages` da conversa e a própria linha (o ledger em `credit_transactions` tem FK
  `related_message_id` → setar NULL nas transações vinculadas antes de apagar as mensagens —
  o consumo continua auditável, só perde o vínculo). Limpa o checkpoint no agents via
  `DELETE /conversations/{thread_id}` (best-effort: falha loga warning). 204.
- **`GET /api/v1/conversations`** ganha `?origin=real|test` (default **`real`**): `real` filtra
  `is_test=false` (lista atual não muda de comportamento), `test` filtra `is_test=true`.

### 3. Créditos — debita normal (decisão de negócio confirmada)

Teste consome tokens reais de LLM. Mesma conversão e mesmo ledger do fluxo real; bloqueio por
saldo esgotado idem (402 + aviso no front com link pra `/creditos`). O consumo de teste entra
nas métricas agregadas (dashboard tenant e admin) normalmente — é consumo real.

### 4. Front (`/conversas`)

- **Abas** no topo da coluna da lista: "Conversas" | "Testes" — estado local do painel, cada
  aba busca com seu `origin`. Trocar de aba limpa a seleção.
- Aba Testes: botão **"Nova conversa de teste"** (chama o POST e seleciona a conversa criada)
  e a lista reutilizando `ConversationList` com rótulo adaptado: conversas de teste exibem
  "Conversa de teste" + data de criação em vez do telefone formatado.
- **`TestConversationThread`** (componente novo, dedicado): chat com bolhas (usuário à
  direita como "Você (cliente)", agente à esquerda), indicador "digitando…" enquanto aguarda a
  resposta síncrona, aviso quando `grouped=true` (mensagem agrupada pelo debounce), erro
  inline sem apagar o histórico, composer sempre ativo, botão "Excluir conversa" (com
  confirmação). **Sem** switch de takeover, heartbeat, popup ou resumo — e sem tocar no
  `ConversationThread` real (histórico de regressões nesse componente).
- Saldo esgotado (402): banner/aviso no composer com link pra `/creditos`.

### 5. Testes

- **api** (unit): criação (201, `is_test`, prefixo `teste-`); `test-messages` — fluxo feliz
  persiste contato+respostas e debita com `ceil`; 409 em conversa real; 402 sem saldo; 502 do
  agents mantém a mensagem do contato; `grouped` não persiste resposta nem debita; DELETE —
  apaga e chama o cleanup do agents, 409 pra conversa real, transações preservadas com
  `related_message_id` NULL; `GET ?origin` filtra dos dois lados e default `real`.
- **web** (unit): abas alternam listas; nova conversa de teste cria e seleciona; thread envia
  e renderiza respostas; indicador digitando; 402 mostra aviso; excluir confirma e remove.

## Documentação

- CLAUDE.md: seção do Painel de Conversas (aba Testes) e "Estado atual do repositório"
  (rotas novas).

## Fora de escopo

- Takeover/resumo em conversas de teste; anexos/mídia; streaming de resposta; mudanças no
  dashboard; renomear/titular conversas de teste; cobrança do cliente final em teste (payload
  `end_customer_billing` nunca enviado — feature age como desabilitada, igual ao playground).
