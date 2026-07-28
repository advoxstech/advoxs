# Billing Gate Determinístico via WhatsApp Interactive Messages — Design

## Contexto

Hoje, quando a cobrança do cliente final está habilitada pro tenant e o contato está sem saldo (primeira mensagem ou saldo esgotado no meio do atendimento), o bloqueio existe — mas vive *dentro* do grafo LangGraph (`agent_node` → `is_billing_blocked`, `apps/agents/agents/nodes.py`). Isso significa que o `agents` service já foi chamado, e o LLM já rodou (custo de tokens gasto), antes de decidir que o cliente não deveria ter sido atendido ainda. O mesmo vale pra oferecer os pacotes e gerar o link de pagamento: hoje isso é feito pela secretária, via LLM + tool (`gerar_link_pagamento_cliente`), quando poderia ser 100% mecânico.

## Objetivo

Mover a decisão "esse cliente tem saldo pra ser atendido?" pra **antes** de qualquer chamada ao `agents`, eliminando o custo de LLM em todo o funil de "sem saldo → escolher pacote → pagar → liberado" — usando um fluxo determinístico baseado em mensagens interativas nativas do WhatsApp Cloud API (`interactive` / `list_reply`) e uma máquina de estados simples no `worker`.

## Decisão de arquitetura

Adiciona um terceiro estado a `conversations.state` (hoje `agent | human`): **`billing_gate`**. Enquanto nesse estado, o `worker` nunca chama o `agents` — ele mesmo conduz um diálogo mecânico (sem LLM) com o contato: mostra os pacotes ativos do tenant como uma lista interativa do WhatsApp, captura a escolha, gera o link de pagamento (reaproveitando o endpoint interno que a tool do `agents` já usa hoje), e aguarda a confirmação do pagamento. Quando o webhook da Stripe do tenant confirma o pagamento, a conversa volta pra `agent` diretamente (sem passar por nenhuma mensagem sintética pro `agents`) e o atendimento retoma de onde estava — como o checkpoint do LangGraph nunca é tocado por essa transição, se a conversa já tinha um especialista atribuído antes do saldo esgotar, ela continua exatamente com ele.

Essa arquitetura torna o gate de billing que hoje vive em `apps/agents` (tool + checagem dentro do `agent_node`) redundante — a garantia "só chega no `agents` quem tem saldo" passa a ser estrutural (decidida antes, no `worker`), não mais uma checagem que o LLM/tool precisa fazer por conta própria. **Remoção é gradual, não imediata** — ver "Rollout gradual por tenant" abaixo: o gate antigo (no `agents`) continua existindo em paralelo até todo tenant estar migrado pro novo, e só então é removido.

## Modelo de dados

- `conversations.state`: constraint `agent | human` vira `agent | human | billing_gate`.
- `conversations.billing_gate_step` (nullable): `aguardando_selecao_pacote` | `aguardando_pagamento` — `NULL` fora do gate.
- `conversations.billing_gate_retries` (integer, default `0`): resetado a cada mudança de estado/step; limite de tentativas antes de escalar pra atendimento humano (`state=human`).
- Sem mudança em `end_customer_balances`/`end_customer_credit_transactions`/`end_customer_credit_packages` — reaproveitados como já existem.
- Sem backfill — conversas existentes continuam em `agent`/`human`; `billing_gate` só é usado a partir do deploy.

## Fluxo

1. **Entrada no gate**: a cada mensagem nova, se `state == agent`, cobrança habilitada, `tenant_billing_settings.insufficient_balance_policy == "deterministic_gate"` (ver "Rollout gradual por tenant") e `end_customer_balances.credit_balance <= 0` → `state = billing_gate`, `billing_gate_step = None`. Tenant ainda em `block_with_message` (o valor padrão de hoje) continua no fluxo antigo, sem nenhuma mudança de comportamento.
2. **Abertura do gate** (`billing_gate_step is None`): mensagem de boas-vindas fixa (2 variantes — primeira compra vs. recompra, ver "Textos determinísticos") + lista interativa com os pacotes ativos do tenant. `billing_gate_step = aguardando_selecao_pacote`.
3. **Seleção de pacote**: resposta `list_reply`/`button_reply` válida → gera o link via `POST /internal/end-customer-billing/checkout` (mesmo endpoint que a tool `gerar_link_pagamento_cliente` já chama hoje) → manda o link → `billing_gate_step = aguardando_pagamento`. Resposta não reconhecida → reenvia a lista, incrementa `billing_gate_retries`; no limite, escala pra `human`.
4. **Aguardando pagamento**: qualquer mensagem do contato nesse step reenvia o link (idempotente — reaproveita a mesma Checkout Session se ainda válida) e incrementa `billing_gate_retries`; no limite, escala pra `human`.
5. **Confirmação de pagamento** (webhook `POST /webhooks/stripe/tenant/{tenant_id}`): credita o saldo (como já faz hoje) e, se `state == billing_gate`, transiciona `state = agent`, zera `billing_gate_step`/`billing_gate_retries`, na mesma transação. A mensagem de confirmação fixa via WhatsApp continua existindo. **Nenhuma chamada ao `agents` é feita nesse ponto** — o cliente retoma o atendimento normalmente na próxima mensagem que mandar.

## Textos determinísticos (sem LLM)

Duas variantes fixas de boas-vindas, configuráveis por tenant (texto institucional opcional, com fallback genérico se não configurado):
- **Primeira compra** (contato nunca teve `purchase` no ledger): mensagem institucional + lista.
- **Recompra** (já teve ao menos 1 `purchase`): mensagem direta de "créditos acabaram" + lista.

## Decisões já resolvidas nesta rodada de design (revisão crítica contra o código real)

- **Parser do webhook**: `apps/api/app/schemas/whatsapp.py::extract_inbound_messages` já trata `type: interactive` hoje (extrai `title`). O trabalho é só garantir que o `worker`, ao processar uma resposta dentro do gate, resolve o pacote escolhido — por comparação de **título** contra os pacotes ativos do tenant (a lista foi montada pelo próprio sistema segundos antes, sem ambiguidade), não por um `id` propagado via schema de `messages`. Decisão: não alterar o schema de `messages`/o parser do webhook além do necessário — resolver a correspondência no `worker`.
- **Endpoint de checkout interno**: `POST /internal/end-customer-billing/checkout` já existe e já é usado pela tool do `agents`, protegido por `INTERNAL_SERVICE_KEY`. Decisão: o `worker` passa a chamar esse mesmo endpoint diretamente — precisa da mesma chave provisionada no seu próprio ambiente (hoje só existe em `api`/`agents`; é um requisito de infra desta mudança, não automático).
- **Conflito com o commit `0e8267e`** (mergeado em paralelo a este design, no mesmo tenant de billing do cliente final): esse commit fez o webhook de pagamento enfileirar uma mensagem de sistema pro `process_inbound_message`, especificamente pra fazer o agente "saber" que o pagamento foi confirmado e completar a transferência sozinho. **Decisão revisada** (depois de formalizar o rollout gradual — ver seção própria abaixo): esse mecanismo **não é removido agora**, só passa a ser condicional. `_send_purchase_confirmation` branch por `insufficient_balance_policy`: tenant em `block_with_message` continua exatamente como `0e8267e` deixou (aciona o `agents` via mensagem de sistema); tenant em `deterministic_gate` usa a transição de estado direta (`billing_gate → agent`, ver "Fluxo" acima) e **não** aciona o `agents`. Remover o caminho antigo por completo só acontece junto da Etapa 5 (depois de 100% migrado).
- **Trabalho da mesma sessão que se torna obsoleto**: o aviso fixo de retorno ao ponto de entrada por saldo esgotado (`bounced_from_billing_block`) e a instrução de prompt que impede revelar `package_id`/exige colar o link literal (ambos em `apps/agents/agents/nodes.py`) vivem inteiramente dentro do bloco de billing que este design eventualmente remove do `agents`. Decisão: aceito como consequência — não há mais "secretária oferecendo pacotes" pra precisar dessas instruções, o fluxo de saldo esgotado passa a ser 100% do `worker`/WhatsApp nativo pros tenants migrados. Só é removido de verdade depois de todo tenant estar em `deterministic_gate` (ver item abaixo) — até lá, esse código continua vivo servindo quem ainda está em `block_with_message`.

## Rollout gradual por tenant

Decisão: **não** é um corte único — a migração é tenant por tenant, reaproveitando a coluna `insufficient_balance_policy` (`tenant_billing_settings`, migration `0014`) que já existe exatamente como hook de extensibilidade pra essa política (hoje só aceita `block_with_message`, sem `CheckConstraint` no banco — é uma `String` livre, então adicionar um valor novo não exige migração de constraint).

- Novo valor aceito: `deterministic_gate`. Continua com `block_with_message` como `server_default` — nenhum tenant existente muda de comportamento sozinho.
- Enquanto um tenant estiver em `block_with_message`: comportamento **idêntico ao de hoje** — o gate vive no `agents` (`is_billing_blocked`, tool `gerar_link_pagamento_cliente`, aviso de retorno, instruções de prompt), sem tocar em `conversations.state`.
- Migrar um tenant pra `deterministic_gate` (via update direto no banco, ou um endpoint/flag futuro fora de escopo deste spec) ativa o fluxo novo só pra ele.
- **A remoção do gate do `agents` (o que a "Decisão de arquitetura" chama de "eventualmente redundante") só acontece depois que 100% dos tenants com cobrança do cliente final habilitada estiverem em `deterministic_gate`** — até então, os dois mecanismos coexistem no código, cada tenant usando o seu. Isso também resolve, por construção, o risco de "janela sem nenhum gate" que a ordem de execução do plano já se preocupava em evitar.
- Critério de "pode remover": nenhum `tenant_billing_settings.enabled = true` com `insufficient_balance_policy != 'deterministic_gate'` — checagem simples de banco antes de abrir o PR que apaga o código do `agents`.

## Fora de escopo

- Mudança no modelo de créditos (pesos, conversão tokens↔créditos) — intocado.
- Mudança no webhook da Stripe em si (assinatura, idempotência por `stripe_payment_id`) — intocado, só ganha a transição de estado.
- Endpoint/UI pra o tenant escolher a própria política de billing — a migração pra `deterministic_gate` nesta primeira leva é operacional (update direto no banco por tenant), não self-service; expor isso no painel é uma etapa futura, não deste design.
- Catálogo de mensagens de boas-vindas mais elaborado (multi-idioma, templates ricos) — só as 2 variantes fixas descritas acima.

## Riscos conhecidos

- **Janela de 24h**: como o contato acabou de mandar mensagem em todos os casos deste fluxo, a janela de atendimento ativo está aberta — não deveria exigir template pré-aprovado da Meta. Validar em ambiente de teste antes de produção.
- **Custo de mensagem de negócio da Meta** (categoria de billing) pra interactive list — separado do custo de LLM que este design corta, mas entra na conta de margem (`pricing_configs`).
- **`billing_gate_retries` precisa resetar a cada mudança de step** — senão um cliente que só demora a pagar (sem errar nada) pode ser escalado pra humano indevidamente.
- **`INTERNAL_SERVICE_KEY` no `worker`**: hoje o padrão do projeto é "falha aberta" quando essa env não está setada (mesmo comportamento já documentado pra `agents`↔`api`) — esquecer de provisionar no `worker` deixaria o endpoint interno sem autenticação nenhuma, não bloqueado.
- **Janela de coexistência dos 2 gates**: enquanto durar o rollout gradual, qualquer bug de regressão precisa ser investigado sabendo qual dos dois caminhos (`block_with_message` no `agents`, ou `deterministic_gate` no `worker`) o tenant afetado está usando — `insufficient_balance_policy` é o primeiro campo a checar em qualquer troubleshooting desse período.

## Testes (estratégia, não exaustivo — detalhamento fica pro plano de implementação)

- Resolução de pacote por título (não por id) dentro do gate, incluindo o caso de título que não bate com nenhum pacote ativo.
- Transição `billing_gate → agent` no webhook Stripe, sem afetar conversas em `human`.
- Máquina de estados completa no `worker`: cada step, fallback de retries, escalonamento pra `human` no limite, reset de `billing_gate_retries` ao mudar de step.
- Idempotência de clique duplicado no link de pagamento (não gera 2 Checkout Sessions).
- Remoção/atualização dos testes que hoje cobrem `is_billing_blocked`, `gerar_link_pagamento_cliente`, o aviso de retorno e as instruções de prompt de `package_id`/link — todos em `apps/agents/tests/unit` — e dos testes de `0e8267e` que verificam o `arq.enqueue_job` revertido nesta mudança.

## Documento relacionado

Elaboração etapa-por-etapa (mais próxima do nível de implementação, já corrigida contra o código real): `docs/superpowers/plans/2026-07-22-billing-gate-deterministico.md`.
