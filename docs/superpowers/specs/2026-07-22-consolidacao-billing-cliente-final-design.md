# Consolidação de billing do cliente final + indicador de ciclo por conversa — Design

## Contexto

Hoje a informação de "gastos e extratos" da cobrança do cliente final está espalhada em 2 lugares de natureza diferente:
- `/configuracoes/cobranca-clientes` (`EndCustomerBillingPanel.tsx`): settings (chaves Stripe, CRUD de pacotes) + `EndCustomerList` (tabela por contato: saldo atual, total comprado e total consumido — **vitalícios**, somam tudo desde o começo).
- Aba "Consumo" dentro de `/conversas` (`ConversationsPanel.tsx`, componente `ConversationsUsageReport`): créditos consumidos por conversa — mas esse relatório é da wallet do **tenant** (escritório↔plataforma), não do cliente final.

`/creditos` (extrato do tenant com a própria plataforma) é uma relação de dinheiro diferente (escritório paga a Advoxs) e **fica fora deste redesenho** — só a parte de cobrança do cliente final (escritório cobra os próprios clientes) está sendo consolidada.

## Objetivo

1. Consolidar em `/configuracoes/cobranca-clientes` as 2 peças que já existem sobre o cliente final, como abas explícitas (mesmo padrão de `/conversas`: `useState` local + botões, `?aba=` na URL só pra deep-link inicial).
2. Adicionar, em cada conversa (lista e cabeçalho da thread), um indicador rápido de **quanto o contato comprou no ciclo atual e quanto já consumiu desse lote** — resetando a cada nova compra.

## Parte 1 — Aba consolidada

`/configuracoes/cobranca-clientes` passa a ter 3 abas (mesmo componente de tabs já usado em `ConversationsPanel.tsx`):
1. **Configurações** — o que já existe hoje em `EndCustomerBillingPanel` (chaves Stripe, `billing_mode`, CRUD de pacotes). Sem nenhuma mudança de comportamento, só passa a viver dentro de uma aba.
2. **Clientes** — a tabela `EndCustomerList` já existente (contato, saldo, total comprado/consumido vitalícios), sem mudança de dado — só muda de "sempre visível abaixo das configurações" para "sua própria aba".
3. **Consumo** — o relatório que hoje vive em `/conversas` (`ConversationsUsageReport`, créditos da wallet do TENANT por conversa) **muda de lugar** pra aqui. Nenhuma mudança de dado/endpoint (`GET /api/v1/conversations/usage` continua igual) — só de onde a tela é renderizada.

`/conversas` perde a 3ª aba — volta a ter só **Conversas** e **Testes**. `ConversationsPanel.tsx`'s `type Tab` perde `"usage"`.

## Parte 2 — Indicador de ciclo de créditos por conversa

**Modelo confirmado com o usuário**: "total" = créditos da compra mais recente daquele contato; "consumido" = quanto já foi gasto desse lote específico, contado a partir da data da última compra. Cada nova compra reinicia os dois números (consumido volta a 0, total passa a ser o valor do novo pacote comprado) — não é mais o modelo vitalício/cumulativo que a aba "Clientes" já mostra (esse continua existindo, sem mudança, na Parte 1).

**De onde vem o dado**: sem migração — deriva do ledger já existente (`end_customer_credit_transactions`). Pra um contato:
- Acha a transação `type="purchase"` mais recente (`created_at` mais alto) → `amount_credits` dela é o "total" do ciclo atual, `created_at` dela é o início do ciclo.
- Soma o valor absoluto de todas as transações `type="consumption"` daquele contato com `created_at` posterior ao início do ciclo → é o "consumido" do ciclo atual.
- Contato sem nenhuma compra ainda → sem ciclo, indicador não aparece (mesmo comportamento de hoje quando `end_customer_balance` é `null`).

**Onde aparece**: nos 2 lugares que já mostram `end_customer_balance` hoje (`ConversationList.tsx` — cada linha da lista — e `ConversationThread.tsx` — cabeçalho da thread), só quando a cobrança do cliente final está habilitada pro tenant. **Complementa, não substitui** — o saldo atual (`end_customer_balance`) continua exatamente como está hoje; o indicador de ciclo aparece do lado, como um texto adicional: "20 de 200 créditos usados" (consumido/total do ciclo atual). Sem ciclo (contato nunca comprou), só o saldo atual aparece, como já acontece hoje.

**Escopo do cálculo**: só entra em `ConversationOut` (lista) e no equivalente da thread — mesmo padrão de opt-in por tenant que `end_customer_balance` já segue.

## Fora de escopo

- `/creditos` (wallet do tenant com a plataforma) — não é tocado.
- Qualquer mudança de schema/migração — o cálculo do ciclo é 100% derivado do ledger existente, em tempo de leitura.
- Histórico de ciclos anteriores (só o ciclo atual é mostrado no indicador rápido — o histórico completo de todas as compras/consumos continua acessível na aba "Clientes", que é vitalícia).
- Mudança na regra de moeda única (turno custeado só pelo cliente final ou só pelo tenant, nunca os dois) — intocada.

## Testes

- Backend: função de cálculo do ciclo testada com — contato sem nenhuma compra (retorna nulo), contato com 1 compra e 0 consumo, contato com 1 compra e consumo parcial, contato com 2 compras (confirma que só conta consumo depois da 2ª/mais recente).
- Frontend: `ConversationList`/`ConversationThread` mostram o indicador só quando o campo vem preenchido; abas novas em `/configuracoes/cobranca-clientes` renderizam os 3 componentes certos; `/conversas` sem a aba "Consumo".
