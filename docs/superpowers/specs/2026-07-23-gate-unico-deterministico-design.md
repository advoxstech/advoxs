# Gate único determinístico — remoção do mecanismo antigo — Design

## Contexto

Duas features anteriores desta mesma sessão construíram o **billing gate determinístico** (`docs/superpowers/specs/2026-07-22-billing-gate-deterministico-design.md`) como um *rollout gradual*: `tenant_billing_settings.insufficient_balance_policy` decide, por tenant, entre o mecanismo **antigo** (`block_with_message`, embutido no grafo do `apps/agents` — a secretária LLM conversa, oferece pacotes, gera link) e o **novo** (`deterministic_gate`, inteiramente no `apps/worker`, mensagens nativas do WhatsApp, zero custo de LLM). Os dois coexistiam de propósito, com a remoção do mecanismo antigo prevista como uma "Etapa 5" futura, condicionada a 100% dos tenants migrados.

Decisão de produto desta sessão: **não vai ter mais rollout gradual — o mecanismo determinístico passa a ser o único que existe**, pra todo tenant, existente ou novo. Isso torna a Etapa 5 atual, não futura.

## Objetivo

- Sempre que `tenant_billing_settings.enabled = true`, o comportamento é sempre o gate determinístico — sem exceção, sem configuração por tenant.
- Todo tenant que já estivesse no mecanismo antigo passa a usar o novo automaticamente, sem ação manual — a própria migration que remove a coluna de policy já cobre isso (não há mais distinção pra migrar).
- O código do mecanismo antigo é removido de `apps/agents` (tool, gate, prompt, testes) — não fica como código morto.
- Nenhuma UI nova — o painel continua só com o toggle `enabled` que já existe em `/configuracoes/cobranca-clientes`.

## Modelo de dados

**Migration nova** (`apps/api`, revision a seguir de `0019`): `DROP COLUMN tenant_billing_settings.insufficient_balance_policy`.

- `billing_gate_welcome_text` (mesma tabela) **permanece** — é customização de texto do tenant pro gate determinístico, não decide mecanismo.
- Não há backfill de dados necessário: como a coluna deixa de existir, todo tenant passa a ter o comportamento determinístico automaticamente, pela ausência de qualquer branch no código que possa escolher outra coisa.
- `downgrade()` recria a coluna com o default histórico (`'block_with_message'`) — reversível, mas sem tentar recuperar o valor específico que cada tenant tinha antes do drop (perdido, aceitável pra uma migration de remoção de feature).

## `apps/worker` — simplificação do gate

- `InboundContext` perde o campo `insufficient_balance_policy`.
- `_load_context` para de selecionar essa coluna (tanto da query em `tenant_billing_settings` quanto do dataclass).
- `apps/worker/app/billing_gate.py::maybe_enter_gate` — a condição de entrada no gate perde o termo `inbound.insufficient_balance_policy == "deterministic_gate"`. Fica: `conversation_state == "agent" and end_customer_billing_enabled and not end_customer_billing_exempt and end_customer_balance <= 0`.
- `apps/worker/app/tables.py` — remove a coluna `insufficient_balance_policy` da Core table `tenant_billing_settings` (mantém `billing_gate_welcome_text`).
- **`process_inbound_message` para de montar/enviar `end_customer_billing` no payload pro `agents`** — como o gate agora intercepta *toda* mensagem com saldo ≤ 0 antes de qualquer chamada ao agente (não só a primeira do contato), o `agents` nunca mais precisa saber saldo/pacotes de ninguém, em nenhum cenário. O bloco `extra_kwargs["end_customer_billing"] = {...}` e o parâmetro correspondente em `apps/worker/app/clients/agents.py::send_message_to_agents` são removidos.
- `customer_funded` (decisão de quem paga o turno) **não muda** — continua `(not exempt) and enabled and balance > 0`, é ortogonal ao mecanismo de gate.

## `apps/api` — fecho do ciclo de pagamento

- `apps/api/app/services/end_customer_billing.py::_send_purchase_confirmation` perde o parâmetro `insufficient_balance_policy` e o branch condicional — sempre faz a transição direta (`conversation.state == "billing_gate"` → `state="agent"`, reset de step/retries), nunca mais monta a `Message` de gatilho nem chama `arq.enqueue_job("process_inbound_message", ...)` — esse enqueue só existia pra fazer o mecanismo antigo (dentro do `agents`) "notar" o pagamento; sem o mecanismo antigo, não há mais ninguém pra notificar dessa forma.
- `process_end_customer_checkout_completed` para de buscar `insufficient_balance_policy` (um `session.scalar` que deixa de existir).
- `apps/api/app/models/end_customer_billing.py::TenantBillingSettings` perde o campo `insufficient_balance_policy`.

## `apps/agents` — remoção completa

Mapeado exaustivamente contra o código real (não é suposição):

- **Tool `gerar_link_pagamento_cliente`** (`agents/tools.py:163-183`) — removida. O client que ela chama, `criar_link_pagamento` (`clients/billing.py`, arquivo inteiro), também é removido.
- **`is_billing_blocked`** (`agents/tools.py:185-190`) — removida.
- **Bloqueio em `transfer_to_agent`** (`agents/tools.py:193-220`) — a tool continua existindo (ainda valida `agent_id` contra `valid_agent_ids`), mas perde os parâmetros `end_customer_billing_enabled`/`end_customer_balance` e a chamada a `is_billing_blocked`.
- **`agent_node`** (`agents/nodes.py`): remove o cálculo de `billing_blocked` (linha 48), o bounce pro ponto de entrada (linhas 50-57), o aviso de retorno fixo (linhas 135-143), a injeção de prompt oferecendo pacotes (linhas 97-118), e a condição de billing na despedida de transferência (linha 149 volta a ser simplesmente `if tool_name == "transfer_to_agent" and not response.content:`).
- **`tool_node`**: remove a injeção de `end_customer_billing_enabled`/`end_customer_balance` em `transfer_to_agent` (linhas 191-194) e a entrada correspondente em `BILLING_GATED_TOOLS` (linha 23) — `STATE_SCOPED_TOOLS` (linhas 17-21) preserva as entradas de outras tools (RAG) que não têm relação com billing.
- **Binding condicional da tool** (linhas 81-84 e a variável `billing_enabled` derivada de `state["end_customer_billing"]`, linha 47) — removido; a lista de tools do agente volta a ser fixa (`transfer_to_agent`, `buscar_base_conhecimento_agente`, `bucar_base_conhecimento_usuario`).
- **`State["end_customer_billing"]`** (`agents/workflow.py:15`) — campo removido do `TypedDict`.
- **`IncomingMessage.end_customer_billing`** (`api/routes.py:53`) — campo removido do schema; a passagem pra `run_agent` (linha 131) e o parâmetro de `run_agent`/entrada no `ainvoke` (`services/call_agent.py:52,85`) removidos.
- **Testes**: dos ~29 testes mapeados em `test_billing_client.py` (2), `test_tools.py` (3 estritamente de billing, dentro de 8 de `transfer_to_agent`), `test_nodes.py` (15), `test_routes.py` (2) — os que exercitam exclusivamente o mecanismo removido são apagados; os que testam `transfer_to_agent`/despedida de transferência sem cenário de billing continuam, só sem os parâmetros/mocks de billing que não existem mais.
- **`apps/agents/API_AGENTS.md`**: atualizar §5 (motor dinâmico) e as seções que documentam a tool/gate removidos.

## `apps/web`

Nenhuma mudança — confirmado explicitamente fora de escopo. O painel continua com só o toggle `enabled`.

## Documentação (`CLAUDE.md`)

Reescrita das seções afetadas:
- "Billing gate determinístico" deixa de se descrever como "rollout gradual, coexiste com o gate acima" — passa a ser simplesmente como a cobrança do cliente final funciona, sem menção a `insufficient_balance_policy` nem a "Etapa 5" (concluída, não é mais pendência).
- "Cobrança do cliente final" (seção mais antiga) perde os bullets que descrevem o gate técnico dentro do `agents` ("Gate técnico no grafo do agents", o aviso de retorno, as instruções de prompt do package_id/link) — esse comportamento não existe mais.
- Modelo de dados: remove `insufficient_balance_policy` da tabela `tenant_billing_settings`.
- Pendências: remove qualquer menção a "migração gradual" como algo em aberto.

## Fora de escopo

- Qualquer mudança de UI em `apps/web`.
- Qualquer alteração no fluxo de `apps/api`'s `test_conversations.py` (conversas de teste) — já é fora de escopo desde a feature anterior.
- Recuperar o valor histórico de `insufficient_balance_policy` por tenant no downgrade da migration.

## Testes

- `apps/agents`: suíte inteira roda depois da remoção (deve passar sem os ~15-20 testes apagados; os testes de `transfer_to_agent` sem billing continuam passando com a assinatura simplificada).
- `apps/worker`: `test_load_context.py`/`test_billing_gate.py`/`test_process_inbound_message.py` perdem os testes específicos de `insufficient_balance_policy`/`block_with_message`; `maybe_enter_gate` ganha confirmação de que a condição de entrada não depende mais de policy.
- `apps/api`: `test_end_customer_billing_service.py` perde os testes que comparavam os dois branches de `_send_purchase_confirmation` (só resta o comportamento único).
