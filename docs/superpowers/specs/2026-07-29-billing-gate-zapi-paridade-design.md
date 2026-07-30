# Billing gate determinístico via Z-API — paridade de provedor

## Motivação

O tenant escolhe livremente entre conectar o WhatsApp pela API oficial da Meta ou pela Z-API (não-oficial, QR code) — os dois caminhos devem entregar o mesmo serviço. Hoje a cobrança do cliente final (billing gate determinístico, `apps/worker/app/billing_gate.py`) está bloqueada pra tenants Z-API porque o gate manda mensagens interativas de lista (`type: "interactive"`/`"list"`) nativas da Cloud API da Meta, sem equivalente implementado na Z-API. Essa spec fecha essa lacuna.

Duas guardas existem hoje e serão removidas por este trabalho:
- `apps/api/app/api/v1/end_customer_billing.py` (~linha 155-166) recusa `PATCH /end-customer-billing/settings` com `enabled=true` quando `whatsapp_numbers.provider == "zapi"`.
- `apps/worker/app/billing_gate.py::maybe_enter_gate` retorna `False` de cara quando `inbound.whatsapp_provider != "meta"` (defesa em profundidade pro caso de um tenant habilitar a cobrança na Meta e depois migrar pra Z-API).

## Descoberta que simplifica o desenho

Em `_package_row` (`billing_gate.py`), o `id` de cada linha da lista **já é o nome do pacote** (`{"id": package["name"], "title": package["name"], ...}`), e a seleção do usuário é resolvida comparando o texto recebido contra esse nome (`_resolve_package_by_title`). Não existe divergência entre "resolver por id" e "resolver por título" no sistema atual — são o mesmo valor.

A Z-API confirmadamente tem um endpoint equivalente de lista interativa (`POST /instances/{id}/token/{token}/send-option-list`) e devolve, no webhook de resposta, um objeto `listResponseMessage: {title, selectedRowId}`. Como `title` já bate 1:1 com o nome do pacote (mesma convenção usada pra Meta), a resolução de seleção **não precisa de nenhum mecanismo novo por provedor** — usar o `title` da resposta como conteúdo da mensagem, exatamente como já é feito pro `list_reply.title` da Meta, é suficiente.

## Arquitetura

### 1. Cliente Z-API do worker — novo arquivo `apps/worker/app/clients/zapi.py`

O worker hoje não tem nenhum cliente Z-API — o billing gate manda mensagem direto (sem passar pelo `agents` service), diferente do fluxo normal de resposta do agente (que delega o envio ao `agents`, cujo `ZApiClient` já existe em `apps/agents/clients/zapi.py`). Segue o mesmo padrão de duplicação deliberada já usado no projeto (`apps/worker/app/clients/whatsapp.py` já duplica o client Meta do `api`, pelo mesmo motivo de isolamento entre serviços deployados separadamente).

Duas funções, mesmo padrão de exceções (`ZApiNetworkError`, `ZApiApiError`) de `apps/api/app/clients/zapi.py`:

```python
async def send_zapi_text_message(
    instance_id: str, token: str, client_token: str | None, to: str, text: str
) -> None:
    """Equivalente a apps/api/app/clients/zapi.py::send_zapi_text_message."""

async def send_zapi_option_list(
    instance_id: str,
    token: str,
    client_token: str | None,
    to: str,
    message: str,
    title: str,
    button_label: str,
    options: list[dict],
) -> None:
    """`options`: `[{"id": str, "title": str, "description": str}]` — mesmo
    formato de linha já usado pra Meta (`_package_row`), sem agrupamento em
    seções (a Z-API não expõe esse conceito). Chama POST .../send-option-list
    com payload `{"phone": to, "message": message,
    "optionList": {"title": title, "buttonLabel": button_label, "options": options}}`."""
```

Mapeamento de campos Meta → Z-API (mesmo texto usado hoje pra Meta, só troca de payload):
- header (`"Pacotes de créditos"`) → `optionList.title`
- body (`"Escolha uma opção:"`) → `message`
- button (`"Ver opções"`) → `optionList.buttonLabel`

### 2. Roteamento por provedor em `billing_gate.py`

Hoje `handle_billing_gate` descriptografa o token da Meta uma vez no topo (`access_token = decrypt_access_token(inbound.access_token_encrypted)`) e passa esse valor como parâmetro por `_open_gate`, `_handle_package_selection`, `_handle_awaiting_payment` e `_send_package_list`. Isso quebra pra Z-API, já que `access_token_encrypted` é sempre `None` nesse caso.

Substituído por dois helpers internos que decidem o provedor e descriptografam a credencial certa a cada chamada — remove o parâmetro `access_token` das 4 funções acima:

```python
async def _send_text(inbound: InboundContext, text: str) -> None:
    """Branca por inbound.whatsapp_provider — Meta (send_text_message) ou
    Z-API (send_zapi_text_message, client_token só descriptografado se
    presente)."""

async def _send_list(inbound: InboundContext) -> None:
    """Monta as opções (_ordered_packages + _package_row) e branca por
    provedor: Meta manda com seções (send_interactive_list_message), Z-API
    manda achatado (send_zapi_option_list)."""
```

### 3. Lista achatada pra Z-API

A Meta suporta seções nomeadas (usadas quando o tenant tem pacote avulso *e* assinatura — 2 seções: "Pacotes de créditos" / "Assinatura mensal"). A Z-API (`send-option-list`) só expõe uma lista flat, sem agrupamento. Extraio a lógica de ordenação hoje embutida em `_packages_to_sections` pra uma função compartilhada:

```python
def _ordered_packages(packages: list[dict]) -> list[dict]:
    """Avulsos primeiro, depois assinaturas — mesma ordem visual usada nas
    seções da Meta."""
```

Usada tanto por `_packages_to_sections` (Meta, agrupada em seções) quanto por uma nova `_packages_to_flat_options` (Z-API, uma lista só) — a `description` de cada linha (já existente em `_package_row`) já diferencia avulso ("R$ X = Y créditos") de assinatura ("R$ X/mês — ilimitado"), então a ausência de cabeçalho de seção não compromete o entendimento.

### 4. Parsing do webhook de entrada — `extract_inbound_zapi_message`

Hoje só reconhece `payload["text"]["message"]`. Estendido pra também reconhecer `payload["listResponseMessage"]["title"]` como conteúdo, quando presente (checado antes do campo `text`, já que uma resposta de lista não vem acompanhada de um campo `text` populado). Sem mudança no schema de `InboundZApiMessage` (`content` continua uma string simples) nem na persistência (`_persist_inbound_message`, `handle_zapi_webhook`) — o dado flui exatamente como já flui pro Meta hoje (`content` vira `messages.content`, lido depois por `_load_context`/`InboundContext.message_content`).

### 5. Remoção das guardas

- `end_customer_billing.py`: remove o bloco que recusa `enabled=true` pra `provider == "zapi"` (a checagem de pacote ativo abaixo dele continua).
- `billing_gate.py::maybe_enter_gate`: remove o early-return `if inbound.whatsapp_provider != "meta": return False` e a docstring que o explica (o docstring da função é atualizado pra não mencionar mais essa limitação).

## Testes

- `apps/worker/tests/unit/test_billing_gate.py`: os 2 testes que hoje afirmam o bloqueio (`test_nao_entra_no_gate_para_tenant_zapi_mesmo_sem_saldo`, `test_ja_em_billing_gate_mas_tenant_migrou_pra_zapi_nao_reprocessa`) são reescritos pra confirmar o oposto — o gate agora entra/persiste normalmente pra Z-API. Novos testes cobrindo `_send_text`/`_send_list` roteando certo por provedor, e `_packages_to_flat_options` com avulso+assinatura misturados.
- Novo `apps/worker/tests/unit/test_clients_zapi.py` (ou arquivo equivalente): cobre `send_zapi_text_message`/`send_zapi_option_list` (sucesso, erro de rede, erro da API) — mesmo padrão de teste já usado pro cliente Z-API do `api`/`agents`.
- `apps/api/tests/unit/` — teste(s) de `extract_inbound_zapi_message` cobrindo o novo caminho `listResponseMessage`; teste(s) de `end_customer_billing.py` removendo/invertendo a asserção do bloqueio por provedor.

## Fora de escopo

- Limite de itens na lista da Z-API (a doc não documenta um teto — mesma postura já adotada hoje pro limite de 10 seções/10 linhas da Meta, também não validado no client).
- `buttonsResponseMessage` (botões simples) — o gate só usa listas.
- Qualquer mudança no fluxo normal de resposta do agente (que já roteia corretamente por provedor via `ZApiClient` do `agents` service) — esta spec cobre só o billing gate, que é o único ponto do sistema que ainda manda mensagem Z-API direto do worker sem passar pelo `agents`.
