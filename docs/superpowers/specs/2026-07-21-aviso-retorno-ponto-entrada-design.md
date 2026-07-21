# Aviso de retorno ao ponto de entrada (saldo do cliente final esgotado) — Design

## Contexto

Hoje, quando a **cobrança do cliente final** está habilitada pro tenant e o saldo do contato esgota no meio de um atendimento com um agente especialista (não o ponto de entrada), o `agent_node` (`apps/agents/agents/nodes.py:50-55`) já devolve silenciosamente a conversa pro ponto de entrada — que então oferece os pacotes de recarga (instrução de sistema já existente, linhas 95-111). O cliente final não recebe nenhuma indicação de que a conversa mudou de agente nem por quê: ele só percebe que "quem responde agora" parece diferente.

Isso é inconsistente com o comportamento já existente pra transferência normal entre agentes (`transfer_to_agent`), que **já avisa o cliente** com uma mensagem fixa ("um momento... vou te passar pra(o) {nome} agora.", `nodes.py:133-140`) antes de trocar de agente.

## Objetivo

Quando a conversa volta pro ponto de entrada por saldo do cliente final esgotado, mandar uma mensagem fixa avisando o que aconteceu, na mesma linha do que já existe pra transferência — sem alterar nada do comportamento de saldo do **tenant** (que continua em silêncio total, de propósito, ver `CLAUDE.md`/seção Billing).

## Mecânica do gatilho

A condição que já existe hoje em `agent_node` —

```python
if billing_blocked and not current["is_entry_point"]:
    current = entry_point
```

— só é verdadeira **uma vez** por bloqueio: como `update["current_agent_id"] = current["id"]` (linha 125) persiste o id do ponto de entrada no checkpoint no mesmo turno, a partir do turno seguinte `current_agent_id` já resolve direto pro ponto de entrada (linha 41-44) e a condição acima nunca volta a ser verdadeira — até o cliente ser transferido de novo pra um especialista (via `transfer_to_agent`, depois de recarregar créditos) e esgotar de novo. Ou seja: **o próprio estado já distingue "acabou de voltar" de "já estava aqui"**, sem precisar de nenhuma flag nova — a mensagem de aviso deve disparar exatamente nesse ponto do código, e só ali.

## Comportamento

Quando essa transição ocorre, o turno produz **2 mensagens** (o `POST /messages` do `agents` já suporta múltiplas respostas por turno — é o mesmo mecanismo que hoje permite `responses: [...]` no retorno):

1. **Aviso fixo** (programático, não gerado pelo LLM — mesmo estilo da despedida de transferência):
   > `"voltando para {nome do ponto de entrada} — o atendimento anterior ficou indisponível porque os créditos acabaram."`

   O nome é interpolado a partir de `entry_point["name"]` (configurável por tenant — não é sempre literalmente "secretária").

2. **Resposta normal do ponto de entrada**: gerada pelo LLM como já acontece hoje, incluindo a oferta de pacotes de recarga (instrução de sistema já existente, sem mudança).

Nenhuma outra combinação (mensagem única concatenada, texto gerado pelo LLM) foi escolhida — decisão do usuário: mensagem fixa e separada, pelos mesmos motivos que a despedida de transferência já é fixa (previsibilidade, testabilidade, não depender do modelo seguir a instrução à risca — há uma pendência conhecida no projeto de o modelo às vezes não seguir instruções de transferência à risca).

## Fora de escopo

- Saldo do **tenant** esgotado (`credit_balance <= 0`): continua em silêncio total, sem nenhuma mensagem — comportamento proposital e intocado por este design.
- Qualquer mudança na instrução de sistema que já oferece os pacotes de recarga (linhas 95-111) — só a mensagem fixa é nova.
- Mudança no formato de retorno de `POST /messages` (`responses: [...]`) — já suporta múltiplas mensagens, nenhuma mudança de contrato necessária.

## Testes

Cobertura em `apps/agents/tests/unit/test_nodes.py` (já existe um teste da transição atual, `test_saldo_esgotado_no_meio_da_conversa_devolve_pro_ponto_de_entrada` — vai precisar de asserção nova pra confirmar a mensagem de aviso como a primeira de `result.update["messages"]`), mais um teste novo confirmando que o aviso **não** aparece quando o bloqueio já vinha do turno anterior (ou seja, quando `current_agent_id` já é o do próprio ponto de entrada) — usando o mesmo `base_state()` default (`entry-1`/`other-1`, `Secretária`/`Condominial`) já usado pelos testes existentes.
