# Ponto de entrada imutável — Design

## Contexto

Hoje (`apps/api/app/api/v1/agents.py`), `is_entry_point` é um campo editável: `POST /agents` aceita `is_entry_point=true` na criação, e `PATCH /agents/{id}` permite promover qualquer agente a ponto de entrada (`_unset_current_entry_point` desmarca o anterior automaticamente). A exclusão só recusa apagar o agente que é o ponto de entrada **atual** (`409`) e o **último** agente do tenant (`409`) — nada impede: promover um agente B via PATCH, e em seguida apagar o agente A (a "secretária" original, agora sem a flag).

Isso viola a regra de produto: só existe **1 ponto de entrada por tenant, decidido uma única vez (no provisionamento), permanente** — nunca escolhido/trocado pelo usuário, e nunca excluível. Há 2 grupos conceituais de agentes: a secretária (entrada única, sempre a mesma) e o resto (que ela direciona e que se direcionam entre si via `transfer_to_agent`).

## Objetivo

Tornar `is_entry_point` **imutável pelo usuário** — só o provisionamento automático (`build_default_agents`, já existente, roda no cadastro self-service e no `seed_dev.py`) decide quem é o ponto de entrada de cada tenant, uma vez, pra sempre.

## Mudanças

### Backend (`apps/api`)

- **`AgentCreate`** (`apps/api/app/schemas/agents.py`): remove o campo `is_entry_point`. `create_agent` (`apps/api/app/api/v1/agents.py`) sempre insere `is_entry_point=False` — nenhum caminho de criação via API pode nascer como ponto de entrada.
- **`AgentUpdate`**: remove o campo `is_entry_point`. `update_agent` nunca mais recebe nem processa esse campo — remove também o bloco que hoje recusa desmarcar sem substituto (fica sem sentido, já que não dá mais pra desmarcar).
- **`_unset_current_entry_point`**: função removida — não há mais nenhum caminho que precise dela.
- **`delete_agent`**: sem mudança na lógica — a checagem `409` já existente (`agent.is_entry_point == True` → recusa) continua, e passa a ser definitivamente à prova de burla, já que não existe mais nenhum jeito de zerar essa flag antes de excluir.
- **Sem migração de dados**: o índice único parcial (`uq_agents_tenant_entry_point`, migration `0015`) já garante exatamente 1 por tenant hoje. Travar a mutação só congela, pra cada tenant, quem já está marcado — sem tocar em nenhuma linha existente.

### Frontend (`apps/web`)

- **`AgentsPanel.tsx`**: remove o checkbox "Ponto de entrada" do formulário de criação. O form passa a ter só nome + instruções.
- **`AgentDetail.tsx`**: remove o toggle de `is_entry_point` da edição. A tela de edição passa a ter só nome + instruções (a seção de base de conhecimento não é tocada por este spec — fica pra uma etapa separada).
- **Badge "ponto de entrada"** na listagem (`AgentsPanel.tsx`) e na tela de detalhe: continua existindo, é só leitura (nenhuma mudança).

## Fora de escopo

- Qualquer mudança na associação de base de conhecimento por agente (attach/detach, upload direcionado) — fica pra um spec separado ("base de conhecimento agrupada por agente").
- Qualquer mudança no provisionamento automático (`build_default_agents`) — já funciona corretamente hoje, não precisa de ajuste.
- Qualquer endpoint ou mecanismo pra, no futuro, permitir trocar o ponto de entrada por uma via administrativa (fora de escopo — decisão deliberada de que isso nunca é permitido, nem pelo tenant nem por um fluxo especial).

## Testes

- Backend: nenhum schema deste projeto usa `extra="forbid"` (confirmado por busca em `apps/api/app/schemas/*.py` — todos usam só `ConfigDict(from_attributes=True)`), então a rejeição estrita de campo desconhecido não é o padrão daqui. Decisão: `POST /agents`/`PATCH /agents/{id}` com `is_entry_point` no corpo simplesmente **ignora** o campo (comportamento padrão do Pydantic pra campo não declarado no schema) — sempre cria com `False`, nunca aceita mudança via `PATCH`. `DELETE` do agente que é ponto de entrada continua `409` (teste já existente, sem mudança). Teste novo: promover-depois-excluir não é mais possível de forma alguma (não existe mais o "promover").
- Frontend: testes de `AgentsPanel`/`AgentDetail` que hoje cobrem o checkbox/toggle são removidos ou adaptados; o teste genérico "renderiza todos os campos do form" é atualizado pra não esperar mais o campo.
