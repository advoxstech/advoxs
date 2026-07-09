# Design — Bloqueio do agente por saldo esgotado

Data: 2026-07-09
Status: aprovado

## Objetivo

Definir e implementar o que acontece quando `tenants.credit_balance` chega a zero ou negativo — pendência registrada no CLAUDE.md ("o saldo pode negativar hoje — o comportamento quando zera segue pendente"). Hoje o `worker` debita créditos sem nenhum limite; esta entrega bloqueia o agente automático quando o saldo se esgota, sem responder ao cliente final e avisando o escritório no painel.

## Decisões de produto

- **Limite: `credit_balance <= 0`, sem buffer/colchão de tolerância.** Enquanto o saldo estiver positivo (mesmo que a execução em andamento vá deixá-lo negativo), o agente responde normalmente — a checagem é feita uma vez, no início do processamento da mensagem, antes de saber quanto a execução vai custar.
- **Reação ao cliente final: silêncio total.** Quando bloqueado, o agente simplesmente não responde. A mensagem do contato já foi persistida pelo `api` antes de enfileirar (isso não muda) e fica visível no painel `/conversas`, esperando um humano do escritório assumir — mesmo comportamento que já existe hoje quando `conversation.state == "human"`. Não é enviada nenhuma mensagem automática ao cliente final (evita vazar informação de billing pra fora do escritório e evita a necessidade de dar ao `worker` a capacidade de enviar WhatsApp, que ele não tem hoje).
- **Aviso ao escritório: banner no painel, sem e-mail.** Reaproveita `GET /api/v1/billing/balance` (já implementado, autenticado, tenant-scoped) — sem infraestrutura de e-mail nova.
- **Checagem antes de chamar o `agents` service**, não depois. Evita gastar tempo/custo real de LLM numa execução que não vai gerar receita.

## Mudança no `worker`

`process_inbound_message` (`apps/worker/app/tasks/messages.py`) passa a consultar `tenants.credit_balance` como parte do contexto que `_load_context` já carrega (mesma função, mesmo `session`, sem round-trip extra de banco). Se `credit_balance <= 0`:

- Loga a decisão (`logger.info`, mesmo padrão do log já existente pro caso `conversation_state != "agent"`).
- Retorna sem chamar `send_message_to_agents` e sem debitar créditos (a função `_debitar_creditos` não é nem alcançada).

Nenhuma mudança de schema — `tenants.credit_balance` já existe e já é lido/escrito pelo próprio `worker`.

## Aviso no painel (`web`)

Componente novo `LowBalanceBanner`: busca `GET billing/balance` no mount (via `backendFetch`, já existente); se `credit_balance <= 0`, renderiza uma faixa fixa de aviso ("Seu saldo de créditos está esgotado — o atendimento automático está pausado." + link "Comprar créditos" para `/creditos`). Se `credit_balance > 0`, não renderiza nada.

- Renderizado nas 4 páginas do painel do tenant, ao lado do `<TenantNav>` já existente em cada uma: `/conversas`, `/base-de-conhecimento`, `/configuracoes/whatsapp`. **Omitido em `/creditos`** — o usuário já vê o saldo e os pacotes ali, o banner seria redundante.
- Não é criado nenhum `layout.tsx` compartilhado nesta entrega — cada página já duplica a renderização do `<TenantNav>` (padrão existente, correção desse ponto fica fora de escopo); o banner segue o mesmo padrão, adicionado individualmente nas 3 páginas.

## Erros e casos-limite

| Caso | Comportamento |
|---|---|
| Saldo exatamente 0 | Bloqueia (limite é `<= 0`) |
| Saldo positivo, execução vai deixá-lo negativo | Permite — checagem é só no início, sem saber o custo da execução em andamento |
| Tenant sem `whatsapp_numbers` conectado | Já tratado hoje (`_load_context` retorna `None` antes de qualquer checagem de saldo) — sem mudança |
| Falha ao consultar o saldo (erro de banco) | Propaga a exceção normalmente — mesmo padrão do resto da função, sem tratamento especial |
| `GET billing/balance` falha no front (rede, 401) | Banner não aparece (fail-safe silencioso) — não bloqueia a navegação nem gera erro visível |

## Testes

- **worker**: saldo `<= 0` não chama `send_message_to_agents`, não debita, retorna sem erro; saldo positivo segue o fluxo normal (teste de regressão do caminho feliz já existente).
- **web**: `LowBalanceBanner` renderiza o aviso quando `credit_balance <= 0`; não renderiza nada quando positivo; link aponta para `/creditos`; falha de rede não quebra a página (fail-safe).

## Fora de escopo desta entrega

- Notificação por e-mail.
- Buffer/colchão de saldo negativo tolerado antes de bloquear.
- Qualquer mudança em `/creditos` (já implementada em feature anterior).
- Criação de um `layout.tsx` compartilhado para as páginas do tenant (débito técnico pré-existente, não introduzido aqui).
