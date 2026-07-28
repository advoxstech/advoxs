# Isenção de cobrança por cliente final — Design

## Contexto

Quando a cobrança do cliente final está habilitada pro tenant (`tenant_billing_settings.enabled`), cada contato paga pelo próprio atendimento pela wallet dele (`end_customer_balances`) — moeda única, nunca o tenant e o cliente ao mesmo tempo (ver seção "Billing / Créditos" do `CLAUDE.md`). O tenant não tem hoje nenhuma forma de isentar um contato específico dessa cobrança: ou o cliente paga (saldo > 0), ou o turno cai no billing gate (antigo, dentro do `agents`, ou o determinístico, dentro do `worker` — ver `docs/superpowers/specs/2026-07-22-billing-gate-deterministico-design.md`).

Este design cobre só isso: um botão no painel de conversas, disponível apenas nas conversas **reais** de WhatsApp (não nas de teste — fora de escopo por decisão explícita), que liga/desliga a isenção de um contato específico.

## Objetivo

- Tenant liga a isenção → confirmação → contato passa a não pagar (o tenant absorve o custo, como já acontece hoje pra qualquer tenant sem cobrança do cliente final habilitada) → contato recebe um aviso via WhatsApp.
- Tenant desliga a isenção → confirmação (avisando que a próxima mensagem já será cobrada normalmente) → cobrança normal retoma → contato recebe um aviso via WhatsApp.
- Se a conversa estiver no meio do billing gate determinístico quando a isenção é ligada, o gate é cancelado e a conversa libera na hora.

## Decisão de arquitetura

**Onde mora a flag**: uma coluna nova, `conversations.end_customer_billing_exempt` (boolean, default `false`). Não é uma tabela nova nem mora em `end_customer_balances` — `conversations` já é 1:1 com o contato dentro do tenant (`UNIQUE(tenant_id, contact_phone_number)`), e `end_customer_balances` só ganha uma linha na primeira compra (um contato isento antes de comprar qualquer coisa não teria onde guardar a flag ali sem criar uma linha vazia só pra isso).

**Quem paga durante a isenção**: o tenant, sempre — mesmo mecanismo de "moeda única" que já existe, só que forçado pro lado do tenant independente do saldo real do cliente final. Não é "ninguém paga" — o custo de LLM continua sendo cobrado de alguém, só não do cliente.

**Como a isenção desliga os dois gates**: o `worker` é o único lugar que decide `customer_funded` (quem paga o turno) e o único que monta o payload `end_customer_billing` enviado ao `agents`. Com o contato isento:
- `customer_funded` é forçado `False` (nunca lê o saldo do cliente final pra essa decisão).
- o payload `end_customer_billing` não é enviado ao `agents` — pro grafo, é como se a cobrança do cliente final estivesse desligada pra esse turno, então o gate antigo (embutido no grafo, usado pelos tenants ainda em `insufficient_balance_policy = "block_with_message"`) não bloqueia nada.
- o billing gate determinístico (`maybe_enter_gate`, `apps/worker/app/billing_gate.py`) nunca transiciona a conversa pra `billing_gate` enquanto isenta.

Isso cobre os dois mecanismos (antigo e novo) com a mesma flag, sem duplicar lógica de gate.

**Cancelamento do gate em andamento**: like ligar a isenção enquanto a conversa está em `billing_gate` (aguardando seleção de pacote ou pagamento) força a saída imediata pra `agent` (`billing_gate_step = NULL`, `billing_gate_retries = 0`) — decisão do usuário. Um link de pagamento já gerado (`billing_gate_checkout_url`) fica órfão (sem problema; se o cliente pagar mesmo assim, o webhook do Stripe credita o saldo normalmente, só não é mais isso que decide se ele é atendido).

**Onde a mudança acontece**: síncrona, no `apps/api`, disparada pelo toggle do painel — não precisa do `worker` nem de fila, porque é uma ação do tenant, não uma mensagem entrante. Só o `apps/worker` precisa aprender a LER a flag (pra decidir `customer_funded`/gate a cada mensagem seguinte).

## Modelo de dados

Migration nova (`0019`), em `apps/api`:
- `conversations.end_customer_billing_exempt` (boolean, `NOT NULL`, `server_default = false`).

Espelhado (sem migração — Core table) em `apps/worker/app/tables.py`.

## Endpoint novo (`apps/api`)

`PATCH /api/v1/conversations/{conversation_id}/billing-exemption`
Body: `{"exempt": bool}`
Resposta: `ConversationOut` (mesmo schema já usado por `GET /conversations`, `PATCH /conversations/{id}`, etc.)

Comportamento:
1. Busca a conversa (tenant-scoped, 404 se não achar — mesmo helper já usado pelas outras rotas).
2. Busca `tenant_billing_settings.enabled` pro tenant — se `False` (cobrança do cliente final não habilitada), `409 Conflict` ("Cobrança do cliente final não habilitada — nada para isentar").
3. **Idempotência**: se `conversation.end_customer_billing_exempt` já é igual ao `exempt` pedido, não faz nada (não reenvia mensagem) — só devolve o estado atual. Evita duplo-clique mandar duas mensagens ao cliente.
4. Se muda pra `exempt=True`:
   - Se `conversation.state == "billing_gate"`: cancela o gate (`state="agent"`, `billing_gate_step=None`, `billing_gate_retries=0`).
   - `conversation.end_customer_billing_exempt = True`.
   - Texto pro cliente: `"A partir de agora, essa conversa é gratuita — você não será cobrado pelo atendimento."`
5. Se muda pra `exempt=False`:
   - `conversation.end_customer_billing_exempt = False`.
   - Texto pro cliente: `"A cobrança normal foi retomada — a partir de agora, o atendimento volta a consumir seus créditos normalmente."`
6. Commit.
7. **Best-effort**: busca o número de WhatsApp conectado do tenant e manda o texto via `send_text_message` (mesmo client já usado por `POST /conversations/{id}/messages`). Falha de envio (sem número conectado, erro da Graph API) só loga um warning — não desfaz a mudança de isenção nem retorna erro pro tenant. Mesma filosofia já usada em `_send_purchase_confirmation` (a mudança de negócio já aconteceu, o aviso é secundário).
8. Devolve `ConversationOut` atualizado.

Sem mensagem persistida em `messages` pra esse aviso — é só um WhatsApp direto, igual o aviso da própria feature de "isenção" não faz parte do histórico de negócio da conversa (diferente da confirmação de pagamento, que vira `Message(sender_type="system")` porque faz parte do funil de compra). Se no futuro isso incomodar (o tenant não vê no histórico que avisou o cliente), é evolução — fora de escopo agora.

## `ConversationOut` — campos novos

- `end_customer_billing_exempt: bool` — estado atual da flag.
- `end_customer_billing_enabled: bool` — se a cobrança do cliente final está habilitada pro TENANT (independente de o contato ter saldo ou não). Necessário pro frontend decidir se mostra o botão: `end_customer_balance` sozinho não serve pra isso, porque um contato isento que nunca comprou nada teria `end_customer_balance = None` mesmo com a cobrança habilitada (a query que popula esse campo já filtra por ter linha em `end_customer_balances`, que só existe após a primeira compra).

Computado com uma query só (`TenantBillingSettings.enabled` pro tenant), reaproveitada por todas as conversas da listagem — não é por contato.

## Worker (`apps/worker`)

- `InboundContext` ganha `end_customer_billing_exempt: bool = False`.
- `_load_context` lê a coluna nova, junto com o SELECT de `conversations` que já existe (mesma convenção de sempre widenar em vez de adicionar query nova).
- `apps/worker/app/billing_gate.py::maybe_enter_gate`: a condição de entrada no gate ganha `and not inbound.end_customer_billing_exempt`.
- `apps/worker/app/tasks/messages.py::process_inbound_message`:
  - `customer_funded = (not inbound.end_customer_billing_exempt) and inbound.end_customer_billing_enabled and inbound.end_customer_balance > 0`
  - o bloco que monta `extra_kwargs["end_customer_billing"]` só roda `if inbound.end_customer_billing_enabled and not inbound.end_customer_billing_exempt`.

Nada muda no `apps/agents` — o grafo já reage à ausência do payload `end_customer_billing` (é exatamente o que acontece hoje pra qualquer tenant sem a feature habilitada).

## Frontend (`apps/web`)

Só em `ConversationThread.tsx` (conversas reais) — **`TestConversationThread.tsx` não é tocado**, por decisão explícita.

- Um switch "Cobrança gratuita" (mesmo estilo visual do switch "IA respondendo" já existente no cabeçalho da thread), visível só quando `conversation.end_customer_billing_enabled === true`.
- Ligar: `window.confirm("Isentar este cliente de cobrança? Ele poderá conversar livremente e você receberá o aviso de que a mudança foi aplicada.")` — mesmo padrão já usado por `handleDelete` nesse mesmo arquivo (sem modal customizado, é o padrão do arquivo).
- Desligar: `window.confirm("A partir da próxima mensagem, esse cliente volta a ser cobrado normalmente. Confirmar?")`.
- Confirmado → `PATCH /conversations/{id}/billing-exemption` com `{exempt: novoValor}` → atualiza a conversa local com a resposta.
- Erro de rede/API → mensagem de erro inline (mesmo padrão de erro já usado pelas outras ações da thread), sem mudar o estado do switch.

`Conversation` (tipo TS) ganha `end_customer_billing_exempt: boolean` e `end_customer_billing_enabled: boolean`.

## Fora de escopo

- Conversas de teste (`TestConversationThread.tsx`, `POST /test-messages`) — não são tocadas por essa feature, mesmo tendo a coluna `end_customer_billing_exempt` disponível no schema (default `false`, nunca setada por elas).
- Histórico/auditoria de quando/quem ligou ou desligou a isenção (sem `Message` persistida, sem log de auditoria dedicado) — só o estado atual da flag.
- Expiração automática da isenção (ex: "isentar só por 24h") — a isenção dura até ser desligada manualmente.
- Isenção parcial (ex: só os primeiros N créditos) — é tudo ou nada.

## Testes

- `apps/api`: novo endpoint (idempotência, 409 sem billing habilitado, cancelamento do gate em andamento, envio best-effort e sua falha não bloqueando a resposta), `ConversationOut`/`_to_conversation_out` com os campos novos.
- `apps/worker`: `_load_context` carregando a flag nova (mesma convenção de widenar o SELECT existente), `maybe_enter_gate` nunca transicionando quando exempt, `customer_funded` forçado `False` quando exempt (mesmo com saldo positivo do cliente final), `extra_kwargs` sem `end_customer_billing` quando exempt.
