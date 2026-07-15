# Takeover repensado + contexto no LangGraph

**Data:** 2026-07-15
**Status:** aprovado

## Problema

1. Mensagens trocadas em modo `human` (takeover) não entram no checkpoint do LangGraph — quando
   a IA reassume, ela não sabe nada do que o atendente e o contato conversaram.
2. Não existe mecânica de retorno de `human` para `agent` (pendência aberta no CLAUDE.md):
   conversa assumida fica assumida pra sempre, mesmo com o atendente fora do painel.
3. Assumir a conversa exige acionar o switch manualmente antes de digitar — atrito no fluxo
   natural de "vi a mensagem, comecei a responder".

## Solução

### 1. Sincronização de contexto com o checkpoint (agents)

Novo endpoint no `agents` (mesma auth de serviço `AGENTS_API_KEY`):

```
POST /conversations/{thread_id}/context
Body: {"messages": [{"role": "contact" | "attendant", "content": "..."}]}
→ 200 {"added": <n>}
```

- Anexa as mensagens ao checkpoint via `aupdate_state` (reducer `add_messages`) — **sem rodar o
  grafo, sem LLM, sem debounce, sem consumo de créditos** (não há tokens).
- Mapeamento: `contact` → `HumanMessage`; `attendant` → `AIMessage` (o atendente fala pelo
  escritório — para o modelo é o "nosso lado" respondendo).
- Body com `messages` vazio ou `role` fora do enum → 400.
- `API_AGENTS.md` documenta o endpoint (fonte da verdade do serviço).

Chamadas (sincronização por mensagem, no momento em que acontece — sem watermark/lote):

- **Resposta do atendente** (`POST /api/v1/conversations/{id}/messages` no `api`): após
  persistir e enviar via Graph API, chama o context com `role="attendant"`. **Best-effort**:
  falha loga warning e não quebra o envio (a mensagem já foi entregue ao contato).
- **Mensagem do contato em modo `human`** (worker, branch que hoje só retorna em silêncio):
  chama o context com `role="contact"` antes de retornar. Best-effort com o mesmo critério.
- **Mensagem do contato com saldo esgotado** (worker, branch de silêncio por
  `credit_balance <= 0`): também sincroniza — custa zero e evita buraco de memória quando o
  tenant recarregar créditos.

Novo client no `api` e no `worker` para essa chamada (reaproveitando o padrão dos clients
existentes de `agents`).

### 2. Timeout de presença (retorno automático pra IA)

- Migration `0009`: coluna `conversations.human_last_seen_at` (timestamptz, nullable).
- **Heartbeat**: `POST /api/v1/conversations/{id}/heartbeat` (autenticado, tenant-scoped) —
  seta `human_last_seen_at = now()`; 204. O front envia a cada ciclo do polling da thread
  (4s, já existente) enquanto a conversa estiver aberta **e** em modo `human`. Presença =
  "a aba está aberta nessa conversa".
- `PATCH state=human` também seta `human_last_seen_at = now()` (takeover começa "presente").
- **Reversão lazy no worker**: ao processar mensagem entrante com `state=human`, compara
  `now - human_last_seen_at` com `HUMAN_TAKEOVER_TIMEOUT_SECONDS` (env do worker, default
  `180`). Expirado (ou `human_last_seen_at` NULL) → `UPDATE state='agent'` e segue o fluxo
  normal do agente na mesma execução (a IA responde já com o contexto do takeover, graças à
  parte 1). Não expirado → sincroniza contexto (parte 1) e permanece em silêncio.
- Sem cron: a IA só precisa reassumir quando chega mensagem — exatamente quando o worker roda.
  Efeito colateral aceito e documentado: a conversa pode aparecer como "manual" na lista até a
  próxima mensagem do contato, mesmo com o atendente ausente.

### 3. Auto-takeover ao focar o composer (web)

- O campo de resposta fica **sempre habilitado** (hoje é desabilitado em modo `agent`).
- Ao **focar** o campo com a conversa em modo `agent`: dispara o `PATCH → human` existente e
  mostra um **popup lateral** (toast fixo à direita da thread): título "IA pausada", texto
  "Você assumiu esta conversa. A IA reassume após 3 minutos sem atividade.", botão
  "Devolver pra IA" (faz o `PATCH → agent` e fecha) e botão de fechar (só dismissa).
- O switch "IA respondendo" continua existindo e funcionando como hoje.
- Se o `PATCH` do auto-takeover falhar, mostra o erro existente e o campo continua editável
  (o guard de 409 do backend segue como backstop).
- Corrida conhecida e aceita: execução do agente já em andamento quando o atendente foca
  ainda produz aquela resposta em voo.

## Testes

- **agents** (unit): context endpoint — happy path (mock do checkpointer, verifica
  `aupdate_state` com `HumanMessage`/`AIMessage` corretos), body vazio → 400, role inválido →
  422/400, auth exigida.
- **api** (unit): heartbeat seta `human_last_seen_at` (204); PATCH pra `human` seta o
  timestamp; `send_message` chama o client de context com `role="attendant"` e não falha a
  request quando o context falha.
- **worker** (unit): modo `human` não expirado → sync chamado, agente não; modo `human`
  expirado → `state` vira `agent` e o agente é chamado; `human_last_seen_at` NULL → tratado
  como expirado; saldo esgotado → sync chamado, agente não.
- **web** (unit): composer habilitado em modo `agent`; foco dispara PATCH e mostra o popup;
  "Devolver pra IA" faz PATCH de volta; heartbeat enviado no ciclo de polling só em modo
  `human`.

## Documentação

- `apps/agents/API_AGENTS.md`: seção do endpoint `/conversations/{thread_id}/context`.
- `CLAUDE.md`: atualizar a seção do painel de conversas (composer sempre ativo, auto-takeover,
  heartbeat/timeout, sync de contexto) e marcar como resolvida a pendência "Mecânica de
  retorno da conversa de `human` para `agent`".

## Fora de escopo

- WebSocket/SSE no lugar do polling (o heartbeat pega carona no polling atual).
- Job/cron pra reverter conversas "manuais" abandonadas sem mensagem nova do contato.
- Sincronizar no checkpoint mensagens anteriores a esta feature (histórico antigo).
