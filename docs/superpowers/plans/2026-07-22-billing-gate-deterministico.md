# Plano: Billing Gate Determinístico via WhatsApp Interactive Messages

> **Revisão (2026-07-22)**: corrigido após checagem linha a linha contra o código real. As mudanças em relação à versão original estão marcadas com **[REVISADO]**. Este documento ainda é um plano de arquitetura (não o formato TDD passo a passo do resto do repo) — antes de implementar, ele precisa passar pelo fluxo normal (`docs/superpowers/specs/` + `writing-plans`) se for adiante.

> Objetivo: eliminar chamadas ao LLM (`agents` service) antes do cliente final ter
> saldo positivo — tanto na primeira compra quanto na recompra — usando
> mensagens nativas do WhatsApp Cloud API (interactive list messages), sem
> nenhum custo de tokens nesse trecho do funil.
>
> Escopo: `apps/api` + `apps/worker`. Remove lógica correspondente de
> `apps/agents`. Não muda o modelo de créditos nem o webhook da Stripe.

## Contexto (por que isso existe)

Hoje, todo turno de conversa passa pelo `agents` service, mesmo quando o
cliente final ainda não pagou nada (primeira mensagem) ou já esgotou o saldo
no meio do atendimento. O bloqueio de saldo esgotado existe, mas vive
*dentro* do grafo LangGraph (`agent_node` → `is_billing_blocked`), ou seja,
o LLM já foi chamado (custo de tokens) antes de decidir que não deveria ter
sido. Este plano move essa decisão para **antes** de qualquer chamada ao
`agents`, usando um fluxo 100% mecânico (sem LLM) para: (1) mostrar os
pacotes, (2) capturar a escolha, (3) gerar o link de pagamento, (4) liberar o
atendimento normal assim que o pagamento é confirmado.

## **[REVISADO] Interação com trabalho recente/paralelo — ler antes de tudo**

Duas coisas mudaram no repo nas últimas horas, **depois** da ideia original
deste plano ter sido escrita, e este plano precisa se posicionar sobre elas
explicitamente antes de qualquer implementação:

1. **Commit `0e8267e` (mergeado em paralelo)**: o webhook de pagamento do
   cliente final (`_send_purchase_confirmation`, em
   `apps/api/app/services/end_customer_billing.py`) agora, além de mandar a
   confirmação fixa por WhatsApp, **também** persiste uma
   `Message(sender_type="system", content="O cliente concluiu o pagamento...")`
   e enfileira `arq.enqueue_job("process_inbound_message", tenant_id=...,
   conversation_id=..., message_id=...)` — a mesma fila que o webhook do
   WhatsApp usa. Isso fazia a Sofia "ver" o pagamento e completar a
   transferência sozinha, sem depender do cliente escrever "já paguei".
   **Esse mecanismo fica redundante e deve ser revertido/removido na Etapa 4
   deste plano** — com o gate determinístico, a transição `billing_gate →
   agent` já acontece direto no banco (sem precisar acionar o `agents`), e o
   checkpoint do LangGraph nunca foi tocado por essa mudança de estado, então
   o `current_agent_id` anterior (se já havia um especialista atribuído antes
   do saldo esgotar) continua valendo — a conversa retoma sozinha no próximo
   turno real do cliente, sem necessidade de nenhuma mensagem de sistema
   sintética disparando o agente. Enfileirar esse job depois deste plano
   estar no ar reintroduziria exatamente o custo de LLM que o plano existe
   pra eliminar (nesse ponto específico, sim, ele derrota o propósito).
2. **Trabalho desta mesma sessão, ainda vivo em `agents/agents/nodes.py`**:
   (a) o aviso fixo de retorno ao ponto de entrada quando o saldo esgota no
   meio da conversa (`bounced_from_billing_block`); (b) a instrução de
   prompt que impede a secretária de revelar o `package_id` ao cliente e a
   obriga a colar o link de pagamento literal na resposta. **Ambos vivem no
   bloco `if billing_blocked and is_entry_point`/`is_billing_blocked` que a
   Etapa 5 remove.** Isso é aceitável — a Etapa 5 já previa remover esse
   bloco inteiro — mas fica registrado aqui que esse código, testado e
   revisado nesta mesma sessão, tem vida curta se este plano for adiante.
   Nenhuma ação extra é necessária além do que a Etapa 5 já descreve; é só
   uma explicitação de custo/trade-off pra quem for aprovar a execução.

---

## Etapa 0 — Modelo de dados

### Migration nova (Alembic, `apps/api`)

1. `conversations.state`: adicionar valor `billing_gate` ao enum/check
   constraint existente (hoje é `CheckConstraint("state IN ('agent', 'human')", name="state")`
   em `apps/api/app/models/conversation.py:29` — vira
   `state IN ('agent', 'human', 'billing_gate')`).
2. Nova coluna em `conversations`:
   - `billing_gate_step` (nullable, string/enum: `aguardando_selecao_pacote`,
     `aguardando_pagamento`) — `NULL` quando `state != billing_gate`.
   - `billing_gate_retries` (integer, default `0`) — resetado sempre que o
     estado muda ou o step avança.
3. Sem mudança em `end_customer_balances` / `end_customer_credit_transactions`
   / `end_customer_credit_packages` — reaproveitados como já existem.

### Backfill

Nenhum necessário — conversas existentes continuam em `agent`/`human`;
`billing_gate` só passa a ser usado daqui pra frente.

---

## Etapa 1 — Webhook (`apps/api`, `POST /api/v1/webhooks/whatsapp`)

**[REVISADO] O parser já trata `type: interactive` hoje** —
`apps/api/app/schemas/whatsapp.py::extract_inbound_messages` já tem:

```python
elif message_type == "interactive":
    interactive = message.get("interactive", {})
    reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
    content = reply.get("title", "")
```

Só extrai o `title` (texto visível pro cliente), não o `id` (o
`end_customer_credit_package_id` que a Etapa 3 precisa pra saber qual pacote
foi escolhido sem ambiguidade de texto livre). O trabalho real desta etapa é
menor do que a versão original assumia:

- Adicionar um campo `interactive_id: str | None = None` em
  `InboundWhatsAppMessage` (`apps/api/app/schemas/whatsapp.py`).
- No `elif message_type == "interactive":`, também extrair
  `reply.get("id")` e propagar pro campo novo.
- `_persist_inbound_message` (`apps/api/app/services/whatsapp_inbound.py`)
  não precisa de mudança de schema em `messages` — o `content` (título) já
  é persistido normalmente; o `id` só precisa chegar até o `worker` de algum
  jeito. Duas opções (decidir na hora de detalhar o plano de implementação):
  (a) guardar o `id` também em `messages.content` de forma estruturada (ex:
  `content = f"{title}|{id}"` — frágil, evitar), ou (b) o `worker`, ao
  processar uma conversa em `billing_gate`, não precisa do `id` vindo da
  mensagem persistida — pode resolver o pacote escolhido comparando o
  **título** recebido contra os pacotes ativos do tenant (já que a lista foi
  montada pelo próprio sistema segundos antes, os títulos são conhecidos e
  não ambíguos). Recomendação: usar (b) e não tocar no schema de `messages`
  — mais simples, sem gambiarra de serialização.
- Dedup por `wa_message_id` continua idêntico.

**Não precisa** de rota nova nem de contrato novo com o `worker` — é o mesmo
pipeline, só um campo a mais capturado no parser (ou nem isso, se a opção
(b) acima for adotada).

---

## Etapa 2 — Cliente de envio (Graph API)

Hoje `apps/agents/clients/whatsapp.py` tem `send_text_message` e
`send_document_message`. Esse fluxo novo **não deve morar no `agents`**
(ver Etapa 5) — criar o equivalente em `apps/api` ou `apps/worker` (o que
fizer mais sentido dado onde o `worker` já lê o token cifrado do tenant):

- `send_interactive_list_message(phone_number_id, access_token, to, header, body, footer, sections)`
  → `POST /{phone_number_id}/messages` com `type: "interactive"`,
  `interactive.type: "list"`.
- Reaproveitar o mesmo retry curto (3 tentativas, só erro transiente) e rate
  limit por número (token bucket Redis) já implementados — extrair pra um
  client compartilhado se for prático, ou duplicar deliberadamente como já é
  feito hoje entre `worker`/`api` para `calcular_creditos` (mesmo padrão do
  projeto).

Limite da Meta: até 10 seções, com no máximo 10 linhas somadas entre todas
as seções — folga grande em relação aos 4 pacotes atuais.

---

## Etapa 3 — Máquina de estados no `worker`

Novo módulo (ex: `apps/worker/app/billing_gate.py`), chamado por
`process_inbound_message` (`apps/worker/app/tasks/messages.py:108`)
**antes** da checagem atual `if inbound.conversation_state != "agent": return`
(linha 125) — essa checagem precisa passar a tratar `billing_gate` como um
terceiro ramo, não cair no mesmo bucket de `human`:

```
process_inbound_message(mensagem recebida):
    conversation = carrega conversation

    # 1. Detecção de entrada no gate
    se conversation.state == "agent":
        se cobrança do cliente final habilitada pro tenant:
            se end_customer_balances.credit_balance <= 0:
                conversation.state = "billing_gate"
                conversation.billing_gate_step = None
                conversation.billing_gate_retries = 0

    se conversation.state == "billing_gate":
        handle_billing_gate(conversation, mensagem)
        return

    # segue fluxo normal (chama `agents` se state == "agent", como hoje;
    # ou a lógica de takeover se state == "human")
```

```
handle_billing_gate(conversation, mensagem):
    if conversation.billing_gate_step is None:
        enviar mensagem de boas-vindas (texto fixo, variante depende de
            "primeira compra" vs "recompra" — ver Etapa 6)
        enviar interactive list message com os pacotes ativos do tenant
            (end_customer_credit_packages where active=true)
            id de cada row = str(package.id)
        conversation.billing_gate_step = "aguardando_selecao_pacote"
        return

    if conversation.billing_gate_step == "aguardando_selecao_pacote":
        if mensagem é list_reply/button_reply:
            # ver Etapa 1 — resolver o pacote pelo título recebido contra
            # os pacotes ativos do tenant (opção (b)), não por um id
            # propagado via schema de messages.
            package = resolve_package_by_title(tenant, mensagem.content)
            if package is None:
                # título não bate com nenhum pacote ativo (ex: lista mudou
                # entre o envio e a resposta) — reenvia a lista, não conta
                # como retry de "não entendi"
                reenviar a lista
                return
            checkout_url = chama POST /internal/end-customer-billing/checkout
                (mesmo endpoint que a tool `gerar_link_pagamento_cliente`
                já usa hoje — ver nota de auth abaixo)
            enviar mensagem de texto com o link
            conversation.billing_gate_step = "aguardando_pagamento"
        else:
            # texto livre / não reconhecido
            conversation.billing_gate_retries += 1
            if conversation.billing_gate_retries >= LIMITE (ex: 3):
                conversation.state = "human"
                conversation.billing_gate_step = None
                # cai no painel /conversas, comportamento já existente
            else:
                reenviar a lista com uma linha de reforço
                    ("Não entendi — escolha uma opção abaixo:")
        return

    if conversation.billing_gate_step == "aguardando_pagamento":
        # cliente mandou mensagem enquanto aguarda pagar
        conversation.billing_gate_retries += 1
        if conversation.billing_gate_retries >= LIMITE:
            conversation.state = "human"
        else:
            reenviar o link de pagamento (idempotente — ver Etapa 4)
        return
```

**[REVISADO] Nota de autenticação**: `POST /internal/end-customer-billing/checkout`
(`apps/api/app/api/v1/internal/end_customer_billing.py:18-22`) hoje é
protegido por `verify_internal_service_key`, comparando contra
`INTERNAL_SERVICE_KEY` — uma env hoje compartilhada só entre `api` e
`agents`. Se o `worker` vai chamar esse endpoint direto, ele precisa dessa
mesma chave provisionada no seu próprio `.env`/ambiente de deploy — isso
**não existe hoje** e precisa entrar no escopo desta etapa (não é
automático só por reusar o endpoint).

**Transição de saída do gate** (billing_gate → agent) acontece em
`process_checkout_completed`, no webhook
`POST /webhooks/stripe/tenant/{tenant_id}` (ver Etapa 4) — não no `worker`.

---

## Etapa 4 — Webhook Stripe do tenant

**[REVISADO]** Esta etapa precisa editar a MESMA função que o commit
`0e8267e` acabou de mudar
(`process_end_customer_checkout_completed`/`_send_purchase_confirmation` em
`apps/api/app/services/end_customer_billing.py`) — não é mais um ajuste
"pequeno" isolado, é uma reconciliação com código que já mudou de forma:

- Depois de creditar `end_customer_balances` e lançar
  `end_customer_credit_transactions` (já existe):
  - Buscar a `conversation` desse `tenant_id` + `contact_phone_number` (já
    existe, reaproveitar a busca que `_send_purchase_confirmation` já faz).
  - Se `state == "billing_gate"`: setar `state = "agent"`,
    `billing_gate_step = None`, `billing_gate_retries = 0` — mesma
    transação do crédito.
- A mensagem de confirmação via WhatsApp (`sender_type="system"`,
  `send_text_message`) já existe e continua igual — best-effort, não desfaz
  o crédito se falhar.
- **Remover** o trecho adicionado em `0e8267e` que persiste a
  `Message(sender_type="system", content="O cliente concluiu...")` e chama
  `arq.enqueue_job("process_inbound_message", ...)`. Com a transição de
  estado direta acima, não existe mais motivo pra acionar o `agents` nesse
  ponto — o checkpoint do LangGraph nunca foi tocado por essa mudança de
  `conversations.state`, então se a conversa já tinha um `current_agent_id`
  (especialista) atribuído antes do saldo esgotar, ela retoma exatamente
  dali no próximo turno real do cliente, sem nenhuma mensagem sintética.
  Reverter também os testes que `0e8267e` adicionou pra essa asserção
  (`test_end_customer_billing_service.py`, os que verificam
  `arq.enqueue_job.assert_awaited_once_with(...)`), e a assinatura de
  `process_end_customer_checkout_completed`/`_send_purchase_confirmation`
  volta a não precisar do parâmetro `arq` (ou mantém, se algum outro motivo
  concreto surgir durante a implementação pra continuar precisando de fila
  aqui — mas hoje nenhuma etapa deste plano depende disso).

**Idempotência do clique duplicado**: antes de chamar
`POST /internal/end-customer-billing/checkout` de novo, checar se já existe
uma Checkout Session criada há pouco pra esse `contact_phone_number` +
`package_id` ainda não expirada/paga — se sim, reenviar o mesmo link em vez
de gerar um novo (evita múltiplas sessões abertas por clique duplo ou
reentrega de webhook da Meta, que é *at-least-once* por design).

---

## Etapa 5 — Simplificação do `agents` service

Remover (documentar a remoção no `apps/agents/API_AGENTS.md` §billing):

- Tool `gerar_link_pagamento_cliente` e seu binding condicional por tenant.
- Bloco `is_billing_blocked` dentro do `agent_node`, incluindo:
  - a injeção de "ofereça pacotes"/instrução de não revelar `package_id`/
    colar o link literal (trabalho desta sessão, ver nota no topo deste
    documento);
  - a `AIMessage` fixa de aviso de retorno ao ponto de entrada
    (`bounced_from_billing_block`, também desta sessão).
- A recusa condicional de `transfer_to_agent` por saldo (a checagem dentro
  da própria tool).
- Campo `state["end_customer_billing"]` propagado em `POST /messages`, se
  não for usado por mais nada depois das remoções acima.

Justificativa a registrar no `API_AGENTS.md`: a partir desta mudança, o
`agents` service só é chamado quando o `worker` já confirmou saldo positivo
do cliente final (ou billing desabilitado pro tenant) — logo, o gate técnico
interno deixa de ter função. **Nota**: isso descarta código testado e
revisado na mesma sessão em que este plano foi escrito (ver seção de
interação no topo) — confirmar que essa troca vale a pena antes de executar
a Etapa 5, não só assumir que "remover é sempre seguro" porque o plano diz
pra remover.

---

## Etapa 6 — Textos determinísticos (sem LLM)

Definir 2 variantes fixas de welcome message (config por tenant, texto
simples, sem geração via LLM):

- **Primeira compra** (contato nunca teve nenhuma `purchase` em
  `end_customer_credit_transactions`): mensagem institucional curta
  ("Olá! Sou o assistente do [Nome do Escritório]. Trabalhamos com [texto
  configurável]. Escolha um pacote pra começar:") + lista.
- **Recompra** (já teve pelo menos 1 `purchase`): mensagem mais curta e
  direta ("Seus créditos acabaram! Escolha um pacote pra continuar:") + lista.

Sugestão de onde guardar o texto institucional: novo campo opcional em
`tenant_billing_settings` (ex: `billing_gate_welcome_text`, nullable — cai
num texto genérico default se não configurado). Fora de escopo mexer no
`insufficient_balance_policy` — este plano *é* a implementação da política
`require_payment_before_agent` mencionada anteriormente, mas sem precisar
criar esse enum agora: `billing_gate` como `state` já cobre o comportamento.
Se quiser manter a extensibilidade documentada no `CLAUDE.md`
(`insufficient_balance_policy`), registrar esse novo `state` como a
implementação de `block_with_message` revisada — ajustar a doc depois de
implementado.

---

## Etapa 7 — Testes

- `apps/api/tests/unit`: extração do `id`/título selecionado no parser
  `interactive` (ajustar os testes já existentes pra esse tipo, não criar
  cobertura do zero — ver Etapa 1 revisada).
- `apps/api/tests/unit`: `process_checkout_completed` transiciona
  `billing_gate → agent` corretamente (e não mexe em conversa que já estava
  em `human`); **remover** os testes de `0e8267e` que hoje verificam
  `arq.enqueue_job` (ver Etapa 4).
- `apps/worker/tests/unit`: máquina de estados completa — cada `step`,
  fallback de retries, transição pra `human` no limite.
- `apps/worker/tests/integration`: idempotência de clique duplicado (não
  gera 2 Checkout Sessions).
- `apps/agents/tests/unit`: remover os testes que cobrem `is_billing_blocked`,
  `gerar_link_pagamento_cliente`, o aviso de retorno
  (`test_saldo_esgotado_no_meio_da_conversa_devolve_pro_ponto_de_entrada` e
  vizinhos) e as 2 instruções de prompt desta sessão
  (`test_instrui_a_nao_revelar_package_id_ao_cliente`,
  `test_instrui_a_colar_o_link_retornado_na_resposta`).

---

## Ordem sugerida de execução (PRs)

1. Migration (`conversations.state` + colunas novas) — isolado, sem
   comportamento novo ainda.
2. Parser do webhook: captura do `id`/comparação por título (menor que o
   original previa) — testável isoladamente.
3. Client de envio de interactive list message + rate limit/retry.
4. Máquina de estados no `worker` + ajuste no webhook Stripe do tenant
   (incluindo a reversão do `arq.enqueue_job` de `0e8267e`) — comportamento
   novo entra em vigor aqui.
5. Remoção do gate/tool do `agents` service — **só depois** do passo 4 em
   produção e validado, pra não deixar uma janela sem nenhum gate.
6. Atualizar `CLAUDE.md` e `API_AGENTS.md` refletindo o estado final.

## Riscos / pontos de atenção a validar durante a implementação

- Janela de 24h: como o contato acabou de mandar mensagem em todos os casos
  deste fluxo, a janela de atendimento ativo está aberta — não deveria
  exigir template aprovado. Confirmar isso no ambiente de teste antes de ir
  pra produção.
- Custo de mensagem de negócio (categoria de billing da Meta) para
  interactive list — separado do custo de LLM que este plano corta, mas
  entra na conta de margem (`pricing_configs`).
- Contador `billing_gate_retries` deve resetar ao mudar de `step`, senão um
  cliente que demora a pagar (não erra o clique) pode ser jogado pra
  `human` indevidamente — cuidado na implementação da Etapa 3.
- **[REVISADO]** `INTERNAL_SERVICE_KEY` precisa ser provisionada no
  ambiente do `worker` (hoje só existe em `api`/`agents`) — sem isso, a
  Etapa 3 quebra silenciosamente em produção (o padrão hoje é "falha aberta"
  quando essa env não está setada, ver CLAUDE.md/seção Cobrança do cliente
  final — risco real de rodar sem autenticação nenhuma nesse endpoint
  interno se for esquecido).
- **[REVISADO]** Corte único sem feature flag por tenant — considerar migrar
  tenant por tenant (`insufficient_balance_policy` ou equivalente) em vez de
  trocar o comportamento de todo mundo no mesmo deploy, dado que é um
  caminho de billing já em produção.
