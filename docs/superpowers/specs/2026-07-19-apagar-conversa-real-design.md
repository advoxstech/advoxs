# Apagar histórico de conversa real — Design

## Contexto

O painel de conversas (`/conversas`) hoje só permite apagar o histórico de **conversas de teste** (`is_test=true`, aba Testes). O endpoint `DELETE /api/v1/conversations/{id}` existe e funciona bem, mas está explicitamente bloqueado (`409 Conflict`) pra qualquer conversa real de WhatsApp. O usuário pediu um botão equivalente pra conversas reais — "apagar de tudo mesmo": mensagens, a conversa em si, e o que o agente lembra dela.

## Abordagem escolhida

**Generalizar o mecanismo existente**, em vez de duplicá-lo num endpoint paralelo. A lógica de exclusão (zerar referência no ledger, apagar mensagens, apagar a conversa, limpar checkpoint do LangGraph) já é sólida e testada para conversas de teste — criar um segundo caminho só pra conversas reais duplicaria essa lógica em dois lugares que precisariam ser mantidos sincronizados pra sempre.

**Alternativas descartadas:**
- *Endpoint paralelo dedicado a conversas reais*: rejeitado por duplicar a lógica de exclusão (ledger + mensagens + checkpoint) em dois lugares — risco de os dois caminhos divergirem com o tempo.
- *Soft delete (arquivar em vez de apagar)*: rejeitado porque contradiz diretamente o pedido — "apagar de tudo mesmo" é hard delete.

## Por que é seguro apagar a linha da conversa inteira

`conversations` tem `UNIQUE(tenant_id, contact_phone_number)`. O webhook de mensagem entrante (`apps/api/app/services/whatsapp_inbound.py`) resolve a conversa com um `SELECT` seguido de `INSERT` condicional (não usa `ON CONFLICT`) — se não encontrar uma conversa pro par `(tenant_id, contact_phone_number)`, cria uma nova do zero (`state='agent'`, `is_test=false`). Ou seja: apagar a linha inteira é seguro — a próxima mensagem desse contato começa uma conversa nova, sem erro de constraint.

Também é seguro em relação a jobs do `worker` em voo: se uma mensagem foi persistida e enfileirada (Arq) mas a conversa é apagada antes do `worker` processar, `_load_context` não encontra a conversa, `process_inbound_message` retorna sem erro (comportamento já existente e testado — mesmo padrão de "conversa/mensagem não encontrada").

## O que muda

### 1. Backend — mover e generalizar o endpoint

- **Remover**: `DELETE /conversations/{conversation_id}` de `apps/api/app/api/v1/test_conversations.py`, junto com a checagem `if not conversation.is_test: raise 409` em `_get_test_conversation`.
- **Adicionar**: `DELETE /conversations/{conversation_id}` em `apps/api/app/api/v1/conversations.py` — mesmo path, agora sem restrição de `is_test`. Funciona para qualquer conversa do tenant autenticado (isolamento por `tenant_id` continua, via `get_tenant_session`). Mesma assinatura de dependências das outras rotas desse arquivo (`get_current_tenant` + `get_tenant_session`), retorna `204 No Content`.
- A lógica de exclusão (hoje em `apps/api/app/services/test_conversations.py:delete_test_conversation`) migra como função privada dentro de `apps/api/app/api/v1/conversations.py` (esse arquivo não tem uma camada de service separada — as demais rotas, como o resumo sob demanda, já implementam a lógica de banco diretamente no módulo da rota; a nova função de exclusão segue essa mesma convenção). `apps/api/app/services/test_conversations.py` perde só essa função — `send_test_message` e o resto do arquivo continuam lá, sem mudança.

### 2. Ledger do cliente final — cobertura nova, que a versão de teste nunca precisou

A versão atual só zera `related_message_id` em `credit_transactions` (ledger do tenant). Conversas reais podem ter débito também em `end_customer_credit_transactions` (quando a cobrança do cliente final está habilitada e o turno foi custeado pela wallet do cliente — ver seção "Cobrança do cliente final" do CLAUDE.md). A versão generalizada zera `related_message_id` nas **duas** tabelas de ledger antes de apagar as mensagens.

**Créditos já consumidos nunca são estornados** — apagar o histórico remove a referência à mensagem, não o lançamento em si. O custo do LLM já ocorreu; desfazer a cobrança seria incorreto.

### 3. Checkpoint do agente (LangGraph)

Reaproveita o client HTTP existente (`apps/api/app/clients/agents.py`), que chama `DELETE /conversations/{thread_id}` no `agents` service — esse endpoint já é agnóstico a teste/real, só precisa do `thread_id` (`"{tenant_id}:{contact_phone_number}"`, mesma convenção nos dois casos). Renomear `delete_playground_conversation` → `delete_agent_checkpoint` (o nome atual sugere uso exclusivo do playground, mas a função sempre foi genérica). Mesmo comportamento: best-effort, fora da transação de banco, falha só gera `logger.warning`.

### 4. Rastreabilidade

Hoje a exclusão (mesmo de teste) não deixa nenhum rastro — nem log. Como a operação passa a valer para conversas reais de cliente, adiciona-se uma linha de `logger.info` estruturado (tenant_id, conversation_id, contact_phone_number) no momento da exclusão, antes do commit. Não é uma tabela de auditoria nova — só uma entrada no log estruturado já usado pela aplicação (stdout JSON, ver seção "Logging / Observabilidade" do CLAUDE.md).

### 5. Frontend

- Novo botão "Excluir conversa" no cabeçalho de `apps/web/src/components/ConversationThread.tsx` (conversas reais), mesmo estilo visual do botão equivalente já existente em `TestConversationThread.tsx` (`font-mono text-[10px] uppercase tracking-[0.15em] text-muted hover:text-danger`).
- Confirmação via `window.confirm`, mesmo padrão usado em todo o painel (exclusão de arquivo de KB, exclusão de pacote de crédito) — texto explícito sobre ser permanente: *"Apagar todo o histórico desta conversa? Essa ação não pode ser desfeita — as mensagens serão excluídas permanentemente."*
- `ConversationThread` ganha uma prop nova `onDeleted` (mesmo contrato já usado por `TestConversationThread`), chamada em caso de sucesso.
- `ConversationsPanel.tsx` passa a mesma função `handleDeleted` (já existe, hoje só usada pela thread de teste) também pra `ConversationThread`.

## Fora de escopo

- Papéis/permissões diferenciados para quem pode excluir — o modelo atual só tem `role=admin`, sem diferenciação; não é o momento de introduzir isso.
- Tabela de auditoria dedicada (histórico de exclusões) — logging estruturado é suficiente para o escopo atual.
- Confirmação em duas etapas (ex: digitar o nome do contato) — mantém o mesmo padrão de `window.confirm` já usado no restante do painel, por consistência.
- Qualquer bloqueio baseado no estado da conversa (`human`/`agent`) ou takeover ativo — a exclusão vale independente do estado.

## Testes

- **Backend**: adaptar os testes existentes de `test_test_conversations_routes.py` (a checagem de 409 pra conversa real deixa de existir — vira teste de sucesso) e mover/adaptar os testes de `delete_test_conversation` para a nova função generalizada, incluindo um teste novo cobrindo a limpeza de `end_customer_credit_transactions.related_message_id`.
- **Frontend**: teste do botão em `ConversationThread.test.tsx` (confirmação, chamada DELETE, callback `onDeleted`), espelhando os testes já existentes de `TestConversationThread`.
