# Design — Switch de IA e Resumo sob Demanda por Conversa

Data: 2026-07-09
Status: aprovado

## Objetivo

Duas melhorias no painel de conversas (`/conversas`): (1) trocar o botão "Assumir conversa"/"Devolver ao agente" por um switch explícito rotulado "IA respondendo" (mesma funcionalidade de takeover já implementada, só o controle visual muda); (2) um resumo de conversa gerado por IA sob demanda, exibido no topo da thread.

## Decisões de produto

- **Switch é reskin, não feature nova** — o `PATCH /api/v1/conversations/{id}` (`{state: "agent"|"human"}`) já existe e não muda. Só a UI do botão vira um switch.
- **Resumo é sob demanda, nunca automático** — cada geração custa uma chamada de LLM (créditos do escritório). Um botão "Resumir conversa" dispara a geração; se já existe um resumo, o botão vira "Atualizar resumo" e mostra quando foi gerado por último.
- **Resumo consome créditos** — mesma conversão tokens→créditos já usada no consumo de mensagens (`ceil(tokens / CREDIT_TOKENS_PER_CREDIT)`), lançada em `credit_transactions` como consumo, com um `related_message_id` nulo (não há mensagem associada — é uma ação sobre a conversa, não uma resposta a ela). Escritório com saldo `<= 0` não pode gerar resumo (mesma regra de bloqueio da Feature de saldo esgotado).
- **A geração do resumo acontece no `agents` service**, não no `api` — é lá que vivem o LLM, a chave da OpenAI e o tracing (Langfuse). O `api` só orquestra: busca as mensagens, chama o `agents`, persiste o resultado e debita os créditos.

## Modelo de dados

Migration nova em `conversations`:
- `summary` (`Text`, nullable) — texto do resumo.
- `summary_generated_at` (`DateTime(timezone=True)`, nullable) — quando foi gerado.

## Mudança no `agents`

Endpoint interno novo **`POST /summaries`** (mesma auth de serviço `verify_api_key` de `POST /messages`). Body: `{messages: [{sender_type, content}]}` (lista simples, sem depender do checkpoint do LangGraph — o `api` já tem o histórico completo em `messages`, não precisa reconstruir via thread_id). Chama o LLM (`ChatOpenAI` já configurado, mesma instância/modelo de `agents/nodes.py`) com um prompt fixo de resumo ("resuma esta conversa entre um cliente e o escritório em até 3 frases, em português, focando no problema/pedido do cliente e no que já foi resolvido"), sem tools, sem grafo — chamada direta ao LLM. Retorna `{summary: str, tokens_used: int}`.

## Mudança no `api`

**`POST /api/v1/conversations/{id}/summary`** (autenticado, tenant-scoped, mesmo padrão de `POST /conversations/{id}/messages`):
1. Bloqueia se `tenants.credit_balance <= 0` → `402 Payment Required` (mensagem indicando saldo esgotado, link implícito pro front tratar).
2. Busca todas as mensagens da conversa (mesma query de `GET /conversations/{id}/messages`, sem paginação — conversas de suporte não chegam a um volume que justifique).
3. Chama `POST /summaries` no `agents` (client novo, mesmo padrão de `app/clients/agents.py` já existente do playground).
4. Salva `summary` + `summary_generated_at` em `conversations`.
5. Converte `tokens_used` em créditos (mesma fórmula do worker) e lança `credit_transactions` (tipo `consumption`, `related_message_id=None`, descrição "Resumo de conversa gerado"), atualiza `tenants.credit_balance` — tudo na mesma transação do passo 4.
6. Retorna `ConversationOut` atualizado (que já ganha os campos `summary`/`summary_generated_at`).

`GET /conversations` e `GET /conversations/{id}` (implícito via `ConversationOut`) passam a incluir `summary`/`summary_generated_at` no payload — sem rota nova pra leitura, o resumo já vem junto da conversa.

## Frontend (`web`)

- **Switch de IA**: no header de `ConversationThread`, o botão atual é substituído por um switch (`role="switch"`, rótulo "IA respondendo", estado ligado/desligado refletindo `conversation.state`). Mesma chamada `PATCH` de hoje ao alternar. O badge "Atendimento manual" continua existindo como está.
- **Resumo**: seção recolhível no topo da thread, abaixo do header. Estado inicial recolhido se não há resumo, expandido se há. Botão "Resumir conversa" (sem resumo) / "Atualizar resumo" (com resumo, mostrando "gerado em {data}"); ao clicar, chama `POST conversations/{id}/summary`, mostra "Gerando…" e desabilita o botão durante a chamada. Erro (`402` por saldo esgotado) mostra mensagem específica ("Saldo de créditos esgotado — não é possível gerar o resumo.") com link pra `/creditos`; outros erros mostram mensagem genérica.

## Erros e casos-limite

| Caso | Comportamento |
|---|---|
| Saldo `<= 0` ao pedir resumo | `402`, front mostra aviso com link pra `/creditos` |
| Conversa sem mensagens ainda | Botão de resumo desabilitado (nada para resumir) |
| Falha do `agents` ao gerar resumo | `502`, front mostra erro genérico, resumo anterior (se houver) permanece visível |
| Gerar resumo duas vezes em sequência | Cada chamada sobrescreve o resumo anterior — sem histórico/versionamento |

## Testes

- **agents**: `POST /summaries` retorna `summary`/`tokens_used`; exige a mesma API key de serviço.
- **api**: `POST /conversations/{id}/summary` bloqueia com saldo `<= 0` (402); no caminho feliz, persiste `summary`/`summary_generated_at`, debita créditos corretamente (ceil), lança `credit_transactions` com `related_message_id=None`; isolamento por tenant (não gera/lê resumo de conversa de outro tenant); `GET /conversations` inclui os campos novos.
- **web**: switch reflete e altera `conversation.state` corretamente (mesmo comportamento do botão anterior, testado como regressão); botão de resumo alterna rótulo com/sem resumo existente; erro 402 mostra a mensagem certa com link.

## Fora de escopo desta entrega

- Resumo automático/contínuo.
- Histórico de resumos anteriores.
- Resumo em outro idioma que não português.
- Qualquer mudança na regra de bloqueio de saldo já implementada (reaproveitada como está).
