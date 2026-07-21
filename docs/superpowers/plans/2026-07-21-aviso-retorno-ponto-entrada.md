# Aviso de retorno ao ponto de entrada Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quando o saldo do cliente final esgota no meio de um atendimento com um agente especialista e a conversa volta pro ponto de entrada, mandar uma mensagem fixa avisando o motivo — antes da resposta normal do ponto de entrada.

**Architecture:** Uma única mudança em `apps/agents/agents/nodes.py::agent_node` — marca a transição especialista→ponto-de-entrada já existente (causada por `billing_blocked`) com uma flag local, e usa essa flag pra prependar uma `AIMessage` fixa à lista de mensagens da resposta desse turno.

**Tech Stack:** Python 3.13, LangGraph (`Command`/`AIMessage`), `apps/agents` (FastAPI). Testes com `pytest-asyncio`, mocks de `ChatOpenAI` (`tests/factories.py`).

## Global Constraints

- A mensagem de aviso é **fixa/programática** — nunca gerada pelo LLM.
- A mensagem de aviso dispara **exatamente uma vez** por bloqueio (no turno exato da transição `not current["is_entry_point"]` → `entry_point`) — nunca se repete nos turnos seguintes em que a conversa já está no ponto de entrada.
- A mensagem de aviso é uma **mensagem separada**, sempre antes da resposta normal do ponto de entrada — nunca concatenada num único texto.
- O texto exato: `f"voltando para {entry_point['name']} — o atendimento anterior ficou indisponível porque os créditos acabaram."`
- Escopo: só o caso de saldo do **cliente final** (`end_customer_billing`). O silêncio total do saldo do **tenant** (`credit_balance <= 0`, tratado no `apps/worker`) não é tocado por este plano.

---

### Task 1: Injetar o aviso fixo na transição de bloqueio

**Files:**
- Modify: `apps/agents/agents/nodes.py:26-145` (função `agent_node`)
- Test: `apps/agents/tests/unit/test_nodes.py:349-393`

**Interfaces:**
- Consumes: nada de fora deste arquivo — só o estado já existente (`billing_blocked`, `current`, `entry_point`, todos já resolvidos dentro de `agent_node`).
- Produces: nada consumido por outra task deste plano (task única).

- [ ] **Step 1: Atualizar o teste existente da transição de bloqueio**

Abra `apps/agents/tests/unit/test_nodes.py` e localize o teste `test_saldo_esgotado_no_meio_da_conversa_devolve_pro_ponto_de_entrada` (por volta da linha 349). Substitua o corpo inteiro por:

```python
@pytest.mark.asyncio
async def test_saldo_esgotado_no_meio_da_conversa_devolve_pro_ponto_de_entrada(monkeypatch) -> None:
    """Saldo esgotado no meio da conversa (não só na transferência inicial) deve
    ser atendido pelo ponto de entrada (equivalente à antiga secretária), que
    oferece os pacotes — em vez de deixar o agente atual responder de graça."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("aqui estão os pacotes disponíveis"))
    monkeypatch.setattr("agents.nodes.model", model)

    result = await agent_node(
        base_state(
            current_agent_id="other-1",
            receptive_message_specialist=False,
            end_customer_billing={
                "enabled": True,
                "balance": 0,
                "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
            },
        )
    )

    assert result.update["current_agent_id"] == "entry-1"
    model.bind_tools.assert_called_once()
    prompt_arg = model.bind_tools.return_value.ainvoke.call_args.args[0][0]
    assert "Básico" in prompt_arg.content

    # Aviso fixo de retorno vem antes da resposta normal do ponto de entrada.
    assert len(result.update["messages"]) == 2
    aviso, resposta = result.update["messages"]
    assert aviso.content == (
        "voltando para Secretária — o atendimento anterior ficou indisponível "
        "porque os créditos acabaram."
    )
    assert resposta.content == "aqui estão os pacotes disponíveis"
```

- [ ] **Step 2: Adicionar o teste de não-repetição**

Logo depois do teste do Step 1 (antes de `test_agente_com_saldo_positivo_nao_e_bloqueado`), adicione:

```python
@pytest.mark.asyncio
async def test_aviso_de_retorno_nao_repete_quando_ja_esta_no_ponto_de_entrada(monkeypatch) -> None:
    """O aviso de retorno só deve aparecer no turno exato da transição
    especialista -> ponto de entrada. Nos turnos seguintes, com
    current_agent_id já apontando pro ponto de entrada, a condição de
    bloqueio (`not current["is_entry_point"]`) nunca mais é verdadeira —
    então o aviso não deve se repetir."""
    from agents.nodes import agent_node

    model = mock_model(ai_response("aqui estão os pacotes disponíveis"))
    monkeypatch.setattr("agents.nodes.model", model)

    result = await agent_node(
        base_state(
            current_agent_id="entry-1",
            receptive_message_specialist=False,
            end_customer_billing={
                "enabled": True,
                "balance": 0,
                "packages": [{"id": "p-1", "name": "Básico", "price_brl": "49.9", "credits_granted": 500}],
            },
        )
    )

    assert result.update["current_agent_id"] == "entry-1"
    assert len(result.update["messages"]) == 1
    assert result.update["messages"][0].content == "aqui estão os pacotes disponíveis"
```

- [ ] **Step 3: Rodar os testes e confirmar a falha**

Run: `cd apps/agents && uv run pytest tests/unit/test_nodes.py -v -k "saldo_esgotado_no_meio_da_conversa or aviso_de_retorno_nao_repete"`
Expected: `test_saldo_esgotado_no_meio_da_conversa_devolve_pro_ponto_de_entrada` FAIL (`assert len(result.update["messages"]) == 2` — hoje só tem 1); `test_aviso_de_retorno_nao_repete_quando_ja_esta_no_ponto_de_entrada` PASS por acidente (já não há bounce nesse cenário, então já não há aviso hoje — mas confirme rodando, não assuma).

- [ ] **Step 4: Implementar o aviso fixo em `agent_node`**

Abra `apps/agents/agents/nodes.py`. Localize o bloco (por volta da linha 50):

```python
    if billing_blocked and not current["is_entry_point"]:
        logger.info(
            "Agente bloqueado por saldo esgotado, devolvendo pro ponto de entrada | agent_id={}",
            current["id"],
        )
        current = entry_point
```

Substitua por:

```python
    bounced_from_billing_block = False
    if billing_blocked and not current["is_entry_point"]:
        logger.info(
            "Agente bloqueado por saldo esgotado, devolvendo pro ponto de entrada | agent_id={}",
            current["id"],
        )
        current = entry_point
        bounced_from_billing_block = True
```

Mais abaixo, localize o bloco que monta o `update` (por volta da linha 125):

```python
    update: dict = {"messages": [response], "current_agent_id": current["id"]}
    if is_first_run:
        update["receptive_message_specialist"] = False
```

Substitua por:

```python
    update: dict = {"messages": [response], "current_agent_id": current["id"]}
    if is_first_run:
        update["receptive_message_specialist"] = False
    if bounced_from_billing_block:
        aviso_retorno = AIMessage(
            content=(
                f"voltando para {entry_point['name']} — o atendimento anterior "
                "ficou indisponível porque os créditos acabaram."
            )
        )
        update["messages"] = [aviso_retorno, response]
        logger.info("Aviso de retorno ao ponto de entrada injetado | entry_point_id={}", entry_point["id"])
```

Não é preciso alterar mais nada nesta função: o bloco de despedida de transferência (mais abaixo, `if tool_name == "transfer_to_agent" and not response.content and not billing_blocked:`) já é pulado quando `billing_blocked` é `True` — e `bounced_from_billing_block` só é `True` quando `billing_blocked` também é `True` — então os dois blocos nunca competem pelo mesmo `update["messages"]` no mesmo turno.

- [ ] **Step 5: Rodar os testes e confirmar sucesso**

Run: `cd apps/agents && uv run pytest tests/unit/test_nodes.py -v`
Expected: todos os testes do arquivo passam, incluindo os dois do Step 1/2.

- [ ] **Step 6: Rodar a suíte completa + lint**

Run: `cd apps/agents && uv run pytest tests/unit -q && uv run ruff check agents/nodes.py tests/unit/test_nodes.py 2>&1 || true`

Expected: todos os testes passam. **Nota**: `apps/agents` tem débito de lint pré-existente conhecido (star-imports em `nodes.py`/`workflow.py`, ver `CLAUDE.md`/"Pendências de CI/CD e testes") — não é esperado introduzir NENHUM erro novo, mas não se surpreenda se `ruff check` sem filtro de arquivo já apontar erros pré-existentes fora do que você tocou. Confirme especificamente que `agents/nodes.py` e `tests/unit/test_nodes.py` não ganharam nenhum erro novo comparando com `git stash` + `ruff check` antes da mudança, se tiver dúvida.

- [ ] **Step 7: Commit**

```bash
git add apps/agents/agents/nodes.py apps/agents/tests/unit/test_nodes.py
git commit -m "feat(agents): avisa o cliente quando volta pro ponto de entrada por saldo esgotado"
```
