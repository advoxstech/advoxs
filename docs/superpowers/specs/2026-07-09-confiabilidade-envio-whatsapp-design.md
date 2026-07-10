# Design — Confiabilidade de Envio no WhatsApp

Data: 2026-07-09
Status: aprovado

## Objetivo

Corrigir uma falha silenciosa real no envio de mensagens do WhatsApp (canal automatizado, via `agents`) — hoje a Graph API pode rejeitar um envio e o `worker` recebe sucesso mesmo assim, persiste a mensagem como enviada e debita crédito do escritório, sem que o cliente final receba nada. Junto disso, tornar a fila de mensagens entrantes (`worker` → `agents`) resiliente de forma consistente com o padrão já usado no projeto (última tentativa tratada, como na ingestão de KB) e sinalizar falhas de forma visível pro escritório, reaproveitando mecanismos já existentes (badge na mensagem, handoff automático pra humano) em vez de construir infraestrutura nova.

## Decisões de produto

- **Falha de entrega nunca é reportada como sucesso.** O contrato de `POST /messages` do `agents` passa a expor, por mensagem, se a entrega ao WhatsApp funcionou.
- **O custo do LLM já incorrido é cobrado independente do resultado da entrega** — `tokens_used`/créditos não mudam quando a entrega falha (o agente já processou e gerou a resposta; a falha é só no canal de saída).
- **Sem infraestrutura nova.** Sem dead-letter table dedicada, sem fila de replay, sem tela de admin nova — a sinalização de falha reaproveita o que já existe: um badge na mensagem (mesmo padrão visual de outros estados) e o handoff automático pra `human` já usado hoje no bloqueio por saldo esgotado.
- **Rate limiting só no canal automatizado** (`agents`) — o takeover humano é ação manual, uma mensagem por vez, sem risco de rajada; não recebe limitador.
- **Retry é sempre curto e não-sofisticado** — poucas tentativas, backoff fixo curto, sem exponential/jitter elaborado. Não há volume hoje que justifique mais que isso.

## Escopo por serviço

### `apps/agents` — cliente WhatsApp e rota `/messages`

- `clients/whatsapp.py` (`WhatsAppClient._safe_request`): ganha retry interno — até 3 tentativas, com backoff fixo de `0.5s` antes da 2ª tentativa e `1s` antes da 3ª — **somente** para erros transitórios (`httpx.TimeoutException`, `httpx.ConnectError`, ou resposta 5xx). Erros 4xx (token inválido, número mal formatado, etc.) falham na primeira tentativa, sem retry — não são transitórios, retry só desperdiçaria tempo. Ao esgotar as tentativas, continua devolvendo `{"success": False, "error": ...}` como já faz hoje (mantém a interface interna atual — não passa a lançar exceção).
- `POST /messages` (`api/routes.py`): o loop que hoje descarta o retorno de `send_text_message` passa a coletar, por mensagem enviada, se a entrega teve sucesso. A resposta da rota ganha um campo novo **aditivo** (não remove nem muda os campos existentes): `delivery_failures: list[int]` — índices (0-based, relativos à lista `responses`) das mensagens que falharam ao entregar. Lista vazia = tudo entregue (inclusive quando `send_to_whatsapp=false`, usado pelo playground — nesse caso nada é tentado, então nunca há falha).
- `tokens_used`/`current_agent` continuam exatamente como hoje — a falha de entrega não afeta o que já foi cobrado.

### Rate limiting (mesmo arquivo, `agents`)

- Token bucket simples via Redis (já usado pelo `agents` para o debounce de rajada — mesma infra, sem dependência nova), chave `whatsapp:ratelimit:{phone_number_id}`, implementado com `INCR` + `EXPIRE 1s` (contador por segundo, não um algoritmo de sliding window — suficiente pro caso de uso e consistente com a simplicidade já usada em `services/concat_messages.py`).
- Antes de cada chamada à Graph API (`send_text_message` e `send_document_message`), consome 1 unidade do bucket; se o limite do segundo já foi atingido, espera (sleep curto) até haver espaço, com um teto de espera de 5s — se não liberar dentro desse teto, trata como falha transitória (entra no mesmo retry de erro 5xx/timeout descrito acima).
- Limite configurável via env nova `WHATSAPP_RATE_LIMIT_PER_SECOND` (default `10` — bem abaixo do limite real da Cloud API, é só uma proteção defensiva, não uma calibração fina).

### `apps/api` (schema) e `apps/worker` — persistência do status de entrega

- Migration nova em `apps/api`: `messages.delivery_status` (`String`, nullable, `CheckConstraint` `IN ('sent', 'failed')`). Nullable porque só é significativo pra mensagens de saída (`sender_type` `agent`/`human`) — mensagens de contato e mensagens já existentes antes desta migration ficam `NULL`, sem retroatividade.
- `apps/worker/app/tasks/messages.py` (`_persist_agent_responses`): ao inserir cada mensagem de resposta do agente, seta `delivery_status = "failed"` se o índice dessa mensagem estiver em `delivery_failures` (devolvido pelo `agents`), senão `"sent"`.
- `apps/api/app/api/v1/conversations.py` (`send_message`, takeover humano): o canal humano **já** propaga falha de verdade hoje (`WhatsAppSendError` → `502`, nada é persistido) — não tem o bug do canal automatizado. Só precisa gravar `delivery_status="sent"` no caminho de sucesso, pra manter o campo consistente entre os dois canais que escrevem em `messages`.
- `apps/worker/app/tables.py` ganha a coluna `delivery_status` na definição Core da tabela `messages` (espelha o schema do `api`, mesmo padrão já usado pras demais colunas dessa tabela).
- `MessageOut` (`apps/api/app/schemas/conversations.py`) ganha `delivery_status: Literal["sent", "failed"] | None`.

### `apps/web` — indicação visual

- `Message` (`lib/types.ts`) ganha `delivery_status: "sent" | "failed" | null`.
- `ConversationThread.tsx` (`MessageBubble`): quando `delivery_status === "failed"`, mostra um badge discreto "Não entregue" (mesma paleta de aviso/`danger` já usada em outros erros da UI) ao lado do horário da mensagem. Sem badge quando `sent` ou `null` (mensagens de contato, ou mensagens antigas sem o campo).

### `apps/worker` — resiliência da chamada `worker` → `agents` (não é sobre entrega ao WhatsApp)

Este item é sobre uma falha diferente: o `worker` não conseguir nem falar com o `agents` (rede, timeout, 5xx do próprio `agents`) — hoje isso já existe e já tem retry, mas de forma incondicional e sem tratamento de última tentativa.

- `process_inbound_message` (`apps/worker/app/tasks/messages.py`): hoje, em qualquer `httpx.HTTPError`, sempre relança `Retry(defer=...)`, deixando o Arq decidir quando desistir (default `max_tries=5`) — quando isso acontece, o job simplesmente some depois do TTL de resultado (1h), sem nenhum rastro visível.
- Alinhar com o padrão já usado em `ingest_knowledge_base_file` (`apps/worker/app/tasks/knowledge_base.py`): checar explicitamente `ctx.get("job_try", 1) < MAX_TRIES` (mesma constante local `MAX_TRIES = 5`, com o mesmo comentário de sincronia manual com o default do Arq) antes de decidir.
  - Se ainda há tentativas: `raise Retry` como hoje, sem mudança de comportamento.
  - Na última tentativa: **não** relança. Loga o erro (nível que dispare atenção) e vira a conversa pra `human` (`UPDATE conversations SET state='human'`) — reaproveitando exatamente o mesmo mecanismo (mesma tabela, mesmo campo, mesma filosofia "o agente não conseguiu processar, um humano precisa assumir") já usado hoje no bloqueio por saldo esgotado.
- Esse handoff automático é disparado **apenas** quando o `worker` não conseguiu de forma alguma chamar o `agents` — não tem relação com falha de entrega ao WhatsApp (que é tratada via `delivery_status`, sem mudar o estado da conversa, já que nesse caso o agente respondeu normalmente, só a entrega ao contato falhou).

## Erros e casos-limite

| Caso | Comportamento |
|---|---|
| Graph API retorna 4xx (token/número inválido) | Falha imediata, sem retry; `delivery_status="failed"` |
| Graph API retorna 5xx / timeout / erro de conexão | Retry (3x, backoff curto fixo); se esgotar, `delivery_status="failed"` |
| Envio humano (takeover) falha | Comportamento atual mantido — `502` pro front, nada persistido; sem mudança |
| Rate limit do bucket local atingido | Espera até 5s por espaço no bucket; se não liberar, trata como falha transitória (entra no retry de envio) |
| `worker` não consegue chamar `agents` (rede/timeout/5xx), ainda há tentativas | Retry com backoff linear (comportamento atual, sem mudança) |
| `worker` esgota as tentativas de chamar `agents` | Não relança; loga erro; vira a conversa pra `human` |
| `send_to_whatsapp=false` (playground) | `delivery_failures` sempre vazio, nenhuma tentativa de envio, nenhum consumo do rate limiter |

## Testes

- **agents**: retry da Graph API cobre os dois ramos (transitório → retry até 3x; 4xx → falha imediata sem retry); rate limiter (bucket vazio espera e libera dentro do teto; teto de 5s excedido → tratado como falha transitória); rota `/messages` propaga `delivery_failures` corretamente por índice, inclusive lista vazia quando tudo entrega ou quando `send_to_whatsapp=false`.
- **api/worker**: migration aplica/reverte limpo; `_persist_agent_responses` grava `delivery_status` correto por índice a partir de `delivery_failures`; `process_inbound_message` só vira a conversa pra `human` na última tentativa (`job_try >= MAX_TRIES`), nunca antes — testado com `job_try` mockado, ciclo RED/GREEN igual ao já usado na feature de isolamento por tenant; envio humano grava `delivery_status="sent"` no caminho de sucesso (sem mudança no caminho de erro, já testado).
- **web**: badge "Não entregue" aparece só quando `delivery_status === "failed"`; ausente quando `"sent"` ou `null`.

## Fora de escopo desta entrega

- Dead-letter table dedicada / tela de replay manual pelo admin.
- Rate limiting no canal humano (takeover).
- Retry/backoff mais sofisticado (exponential com jitter completo) — backoff curto fixo é suficiente por agora.
- Suporte a mensagem template (contato proativo fora da janela de 24h) — pendência separada, sem decisão tomada, não relacionada a este spec.
- Qualquer mudança no comportamento de bloqueio por saldo esgotado (mecanismo reaproveitado como está, não alterado).
- Calibração fina do limite de rate limiting contra os limites reais da Meta — o valor default é só uma proteção defensiva conservadora, não uma calibração baseada em dados de uso real.
