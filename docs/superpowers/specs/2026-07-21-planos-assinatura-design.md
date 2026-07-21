# Planos de Assinatura (Agentes, Ferramentas, Base de Conhecimento) — Design

## Contexto

Hoje o cadastro self-service (`/`) vende só **pacotes de créditos** — o tenant escolhe um dos 4 pacotes (Starter/Growth/Scale/Enterprise), paga uma vez via Stripe Checkout (modo `payment`, sem recorrência) e ganha um lote fixo de créditos. Não existe nenhum conceito de "plano": todo tenant, uma vez cadastrado, pode criar **quantos agentes quiser** (Etapa 1-3, já em produção) e todo agente usa o mesmo conjunto fixo de tools genéricas, de graça. O único limite hoje é de **storage total de KB por tenant** (`KB_MAX_TOTAL_SIZE_BYTES`, 500 MB, igual pra todo mundo, configurável só por env — já documentado como pendência "variação por plano fica pra depois").

Esta mudança introduz **planos de assinatura mensal recorrente**, que passam a governar:
- quantos **agentes** o tenant pode ter,
- quantas **ferramentas extras** (dimensão reservada — ver "Fora de escopo") o tenant pode habilitar,
- quantos **arquivos** (e quantos MB) a base de conhecimento do tenant pode ter,
- quantos **créditos** o tenant recebe de bônus todo mês.

O consumo de créditos (débito por execução de agente) **não muda** — continua a mesma fórmula ponderada por tokens já existente. Comprar mais créditos via `/creditos` (pacotes avulsos, sem recorrência) **também não muda** — continua sendo o jeito principal de manter a operação rodando; os créditos do plano são um bônus mensal, não a fonte primária.

## Decisões de produto já tomadas

1. **Plano = assinatura mensal recorrente** (Stripe Subscriptions), não uma compra única. Trocar de plano muda a assinatura.
2. **"Ferramentas extras" é uma dimensão reservada pro futuro.** Hoje nenhuma tool extra existe — todo agente, em qualquer plano, já tem de graça as tools básicas atuais (transferir, buscar na própria KB, buscar na KB do contato, gerar link de pagamento). O plano já guarda um teto pra esse contador, mas **nenhuma tool nova é construída nesta entrega** — zero enforcement de verdade até o catálogo existir.
3. **Só upgrade nesta v1** (nunca downgrade), com **proration** — o Stripe cobra a diferença proporcional na hora da troca.
4. **Limite de arquivos de KB também varia por plano** (contagem de arquivos, não só storage total) — fecha a pendência já documentada no `CLAUDE.md`.

## Os planos

| Plano | Preço/mês | Agentes | Ferramentas extras | Arquivos de KB | Storage de KB | Créditos inclusos/mês |
|---|---|---|---|---|---|---|
| **Essencial** | R$ 97 | até 5 | 0 | até 50 | 250 MB | 300 |
| **Profissional** | R$ 247 | até 12 | até 3 | até 150 | 750 MB | 1.000 |
| **Escritório Completo** | R$ 497 | até 30 | até 8 | até 400 | 1,5 GB | 3.000 |

Os números acima são a proposta inicial — o usuário já indicou que vai reajustar as proporções depois; a estrutura (5 dimensões por plano, escalando junto) é o que importa pra esta entrega, não os valores exatos.

**Essencial permite até 5 agentes** porque todo tenant novo já nasce com 4 (secretária + 3 especialistas padrão, ver `default_agents.py`) — sobra espaço pra 1 agente customizado sem forçar upgrade imediato no primeiro dia.

## Pacotes de créditos (recompra, `/creditos`) — sem mudança

Continuam os mesmos 4, já em produção:

| Pacote | Preço | Créditos | Bônus |
|---|---|---|---|
| Starter | R$ 100 | 1.000 | — |
| Growth | R$ 250 | 2.750 | +10% |
| Scale | R$ 500 | 6.000 | +20% |
| Enterprise | R$ 1.000 | 13.000 | +30% |

Nomenclatura deliberadamente diferente da dos planos (inglês vs. português) — nunca confundir "assinar o Profissional" com "comprar o pacote Growth".

## Modelo de dados

### `subscription_plans` (global, não tenant-scoped)

- `id` (uuid, PK)
- `name` (string — "Essencial", "Profissional", "Escritório Completo", "Legado")
- `price_brl` (numeric)
- `max_agents` (integer, **nullable = sem limite**)
- `max_extra_tools` (integer, nullable — reservado, sem enforcement nesta entrega)
- `max_knowledge_base_files` (integer, nullable = sem limite)
- `max_knowledge_base_storage_bytes` (bigint, nullable = sem limite)
- `monthly_credits_granted` (integer, default `0`)
- `is_legacy` (bool, default `false`) — marca o plano especial de migração (ver "Migração de tenants existentes"); nunca aparece na tela pública de planos
- `active` (bool, default `true`)
- `created_at`, `updated_at`

`max_knowledge_base_storage_bytes` substitui, como fonte da verdade por tenant, o antigo `KB_MAX_TOTAL_SIZE_BYTES` (env global) — esse env continua existindo só como **fallback** pra tenants sem assinatura resolvível (não deveria acontecer em operação normal, é só rede de segurança). `KB_MAX_FILE_SIZE_BYTES` (limite por arquivo individual, 20 MB) continua global — não faz sentido variar por plano.

### `tenant_subscriptions` (tenant-scoped, 1:1 com tenant)

- `id` (uuid, PK)
- `tenant_id` (FK → `tenants`, `UNIQUE`)
- `plan_id` (FK → `subscription_plans`)
- `stripe_subscription_id` (nullable, unique — `NULL` só para tenants no plano Legado, que não têm assinatura Stripe de verdade)
- `status` (`active` | `past_due` | `canceled`) — espelha o status da assinatura no Stripe, atualizado via webhook
- `current_period_end` (timestamptz, nullable) — fim do ciclo de cobrança atual; `NULL` pra tenants Legado (nunca expira)
- `created_at`, `updated_at`

RLS igual às demais tabelas tenant-scoped.

### Sem mudança de schema em `credit_transactions`

A concessão mensal de créditos do plano usa o mesmo ledger já existente: `type="bonus"`, `related_message_id=NULL`, `credit_package_id=NULL`, `stripe_payment_id` = o id da invoice do Stripe que gerou a concessão (reaproveita a coluna já existente, dá idempotência: antes de conceder, checa se já existe uma linha com esse `stripe_payment_id`).

## Fluxo de cadastro (`/`) — muda de "escolher pacote" pra "escolher plano"

A tela pública de cadastro passa a listar os 3 planos (nunca o Legado) em vez dos 4 pacotes de crédito. O backend (`POST /api/v1/signup/checkout`) cria uma Stripe Checkout Session em **modo `subscription`** (era `payment`) — o preço é construído inline via `price_data` com `recurring: {interval: "month"}` (mesmo padrão inline já usado pros pacotes de crédito, só que agora recorrente — sem precisar pré-criar objetos `Price` no Dashboard da Stripe). Metadata da sessão ganha `flow="assinatura"` (novo valor, ao lado do já existente `flow="recompra"` — ausência do campo continua sendo o fluxo antigo de pacote de crédito único, mantido só pra não quebrar nenhuma Checkout Session já criada antes do deploy, mas **nenhuma tela nova aponta pra esse fluxo**; é candidato a remoção numa limpeza futura, fora de escopo aqui).

O webhook (`POST /api/v1/webhooks/stripe`, `checkout.session.completed`) ganha um terceiro ramo em `process_checkout_completed`, chaveado por `flow="assinatura"`: cria `tenant` + `user` (mesma lógica de hoje) + `tenant_subscriptions` (status `active`, `stripe_subscription_id` = `session.subscription`, `current_period_end` resolvido via `stripe.Subscription.retrieve(...)`) — **sem** lançar `credit_transactions` aqui (a concessão de créditos, mesmo a do primeiro mês, sai só do evento de invoice, ver seção seguinte — um único caminho de código concede créditos, nunca dois). Auto-login (token one-time) continua igual, reaproveitado dos dois fluxos.

## Concessão mensal de créditos

Todo ciclo pago (o primeiro e cada renovação) dispara `invoice.payment_succeeded` no Stripe. Novo handler no webhook: resolve `tenant_subscriptions` pelo `subscription` id do invoice, busca `subscription_plans.monthly_credits_granted`, e — se ainda não existe uma linha em `credit_transactions` com esse `stripe_payment_id` (id do invoice) — lança `type="bonus"` com esse valor e soma em `tenants.credit_balance` (mesmo lock `FOR UPDATE` já usado nos outros débitos/créditos da wallet). Idempotente contra retry do Stripe, mesmo padrão de auditoria já usado pelas compras de pacote.

⚠️ **Ordem de chegada não é garantida pelo Stripe**: o invoice do primeiro ciclo pode, em teoria, chegar antes do `checkout.session.completed` que cria o `tenant_subscriptions`. Se isso acontecer, `tenant_subscriptions` ainda não existe — o handler devolve um erro (não-2xx) em vez de silenciar, o que faz o Stripe reentregar o webhook com backoff automático; na prática, o `checkout.session.completed` já terá processado a essa altura. Mesmo princípio de "falhar alto pra deixar o retry nativo do Stripe resolver" já vale pros webhooks existentes.

## Troca de plano (upgrade)

Novo endpoint `POST /api/v1/billing/subscription/upgrade` (`{plan_id}`, autenticado, `tenant_id` sempre do JWT): valida que `plan_id` é um plano público (`is_legacy=false`, `active=true`) e que o novo plano é estritamente maior que o atual em pelo menos uma dimensão (guarda simples contra "downgrade disfarçado de upgrade" — comparação por `price_brl` é suficiente pra v1, já que os 3 planos públicos têm preço e capacidade sempre crescendo juntos). Chama `stripe.Subscription.modify(sub.stripe_subscription_id, items=[...], proration_behavior="create_prorations")`, e **na mesma request** (síncrono, sem esperar webhook) atualiza `tenant_subscriptions.plan_id` — o Stripe cobra a diferença proporcional automaticamente na próxima fatura/imediatamente, conforme o comportamento padrão de `create_prorations`. `customer.subscription.updated` (webhook) fica só como rede de segurança pra sincronizar `status`/`current_period_end` caso divirja, não é o caminho principal de atualizar `plan_id`.

Front (`apps/web`): tela nova ou seção em `/perfil` (a decidir na etapa de implementação) mostrando o plano atual e um botão de upgrade por plano superior disponível.

## Aplicação dos limites

Dois pontos de enforcement, ambos checando **contagem atual vs. limite do plano** (nunca confiando em cache, sempre uma query fresca) antes de criar:

- **`POST /api/v1/agents`** (criar agente): conta `agents` do tenant; se `count >= plan.max_agents` (e `max_agents` não for `NULL`) → `409` ("Seu plano atual permite até N agentes — faça upgrade pra criar mais").
- **`POST /api/v1/knowledge-base/files`** (upload): além das checagens já existentes (extensão, tamanho por arquivo, nome duplicado), conta `knowledge_base_files` do tenant contra `plan.max_knowledge_base_files`, e soma `size_bytes` contra `plan.max_knowledge_base_storage_bytes` (substitui o `KB_MAX_TOTAL_SIZE_BYTES` global só nesta checagem) → `409` no que exceder primeiro.

`max_extra_tools` **não tem nenhum ponto de enforcement nesta entrega** — não existe ainda o que contar.

Assinatura com `status != "active"` (inadimplente ou cancelada) bloqueia esses dois mesmos endpoints (`409`, mensagem genérica de "assinatura pendente") — **não afeta** se os agentes respondem no WhatsApp (isso continua governado só por `credit_balance`, sem mudança, os dois mecanismos ficam ortogonais como já são hoje pro cliente final).

## Migração de tenants existentes

Uma migration cria `subscription_plans` (seed dos 3 planos públicos + 1 linha `is_legacy=true` chamada "Legado", com todos os limites `NULL` — sem teto — `price_brl=0`, `monthly_credits_granted=0`) e `tenant_subscriptions`, e faz backfill: **todo tenant já existente** ganha uma linha em `tenant_subscriptions` apontando pro plano Legado, `stripe_subscription_id=NULL`, `status="active"`, `current_period_end=NULL`. Isso preserva exatamente o comportamento de hoje (sem limite de agentes/KB) pra quem já é cliente — ninguém é bloqueado retroativamente por já ter mais agentes do que o Essencial permitiria. **Todo cadastro novo, a partir do deploy desta feature, escolhe obrigatoriamente um dos 3 planos públicos** — não há mais caminho de cadastro gratuito/sem plano.

## Fora de escopo (nesta entrega)

- Catálogo de ferramentas extras — a coluna `max_extra_tools` existe, nada mais.
- Downgrade de plano.
- Cancelamento de assinatura pelo próprio tenant (self-service) — fica como ação manual/suporte por ora; o campo `status` já modela o estado, só falta a ação de UI.
- Decidir o que acontece com agentes/arquivos que já excedem o teto de um plano MENOR (não existe hoje, porque só upgrade é permitido e todo tenant novo começa dentro do teto do próprio plano).
- Remover o ramo antigo (pacote de crédito único no cadastro, `flow` ausente) — mantido por compatibilidade com sessões já criadas, mas nenhuma UI nova o usa.

## Testes

- Unidade: seed dos planos, `_get_active_subscription` (helper compartilhado pelos dois pontos de enforcement), os dois `409` de limite (agentes e KB, por contagem e por storage), o `409` de assinatura não-ativa, a concessão mensal idempotente por `stripe_payment_id` do invoice, o guard de upgrade-só-pra-cima, o branch novo de `process_checkout_completed` (`flow="assinatura"`) não lançando `credit_transactions` (só a criação de `tenant_subscriptions`).
- Migration: backfill testado com um tenant seed real, plano Legado sem limite algum confirmado.
