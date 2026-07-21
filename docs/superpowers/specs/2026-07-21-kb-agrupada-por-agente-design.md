# Base de conhecimento agrupada por agente — Design

## Contexto

Hoje a associação de arquivo-a-agente é N:N (tabela `agent_knowledge_base_files`) e é gerenciada em 2 telas: `/agentes/[id]` (`AgentDetail.tsx` — lista de arquivos anexados, attach/detach, link de upload direto) e `/base-de-conhecimento` (`KnowledgeBasePanel.tsx` — upload com `<select>` de agente de destino, listagem linear sem nenhuma indicação de a qual agente cada arquivo pertence).

O usuário pediu pra consolidar essa gestão inteira em `/base-de-conhecimento`, organizada visualmente por agente (formato de árvore), e simplificar `/agentes/[id]` — que não deve mais gerenciar KB, só mostrar um resumo.

## Objetivo

`/base-de-conhecimento` passa a ser o único lugar que gerencia a relação arquivo↔agente, apresentada como uma árvore expansível (1 pasta por agente, arquivos como folhas — um arquivo em mais de um agente aparece em cada pasta correspondente, preservando o N:N já existente). `/agentes/[id]` perde toda a gestão de KB, ficando só com um resumo somente-leitura e um link.

## Decisões (já discutidas e aprovadas com o usuário)

1. **N:N preservado** — um arquivo pode continuar anexado a mais de 1 agente; na árvore, ele aparece em cada pasta correspondente (não existe "pasta principal" nem duplicação de dado, é o mesmo arquivo listado em múltiplos grupos).
2. **Árvore expansível de verdade** (não seções fixas) — 1 nó por agente (nome + contagem de arquivos), expansível/recolhível, incluindo agentes com 0 arquivos.
3. **Upload por pasta** — cada pasta de agente tem seu próprio botão de upload, já sabendo o destino (sem `<select>` de agente no topo da página).
4. **"Anexar a outro agente" some de `AgentDetail.tsx` e reaparece por arquivo, dentro da árvore** — cada arquivo-folha ganha uma ação que abre um seletor pequeno com os agentes que ainda não têm esse arquivo anexado.
5. **`AgentDetail.tsx` mantém um resumo somente-leitura** ("N arquivo(s) anexado(s)") com link pra `/base-de-conhecimento?agent_id={id}` — sem attach/detach ali. O link passa a abrir a árvore com a pasta daquele agente já expandida (troca o comportamento atual do parâmetro, que hoje pré-seleciona um `<select>` de upload que deixa de existir).
6. **Desanexar o último vínculo de um arquivo passa a ser bloqueado com `409`** — mesmo padrão já usado pra não deixar o tenant sem agente/sem ponto de entrada (ver spec "ponto de entrada imutável"). Hoje isso é permitido e deixa o arquivo "sem agente" (invisível pra qualquer busca de RAG, já que nenhum agente o consulta mais) — decisão: nunca permitir esse estado; pra remover de verdade, o usuário exclui o arquivo (ação que já existe e cobre esse caso).

## Mudanças

### Backend (`apps/api`)

- **`KnowledgeBaseFileOut`** (`apps/api/app/schemas/knowledge_base.py`): ganha o campo `agent_ids: list[uuid.UUID]`.
- **`GET /knowledge-base/files`** (`apps/api/app/api/v1/knowledge_base.py`): a query passa a fazer join com `agent_knowledge_base_files` e agrupar os `agent_id`s por arquivo, populando o campo novo. É o único dado que faltava pra montar a árvore no frontend (a lista de agentes em si já vem de `GET /agents`, endpoint já existente e intocado).
- **`DELETE /agents/{id}/knowledge-base-files/{file_id}`** (`apps/api/app/api/v1/agents.py`): antes de apagar o vínculo, conta quantos vínculos aquele `knowledge_base_file_id` tem no total; se for o único (`count <= 1`), recusa com `409` ("não é possível desanexar o último agente deste arquivo — exclua o arquivo se não for mais usar").
- **Sem mudança**: `POST /agents/{id}/knowledge-base-files` (attach), `POST /knowledge-base/files` (upload, `agent_id` continua opcional com fallback pro ponto de entrada — usado pelo botão de upload de cada pasta, que agora sempre manda o `agent_id` explícito), `GET /agents/{id}/knowledge-base-files` (endpoint por agente — continua com consumidor real: `AgentDetail.tsx` o usa pra calcular a contagem do resumo, ver seção Frontend abaixo).

### Frontend (`apps/web`)

- **`KnowledgeBasePanel.tsx`** (reescrita): busca `GET /agents` (lista completa, incluindo `is_entry_point` pra rotular "(ponto de entrada)") e `GET /knowledge-base/files` (agora com `agent_ids`). Monta a árvore no cliente: para cada agente, filtra os arquivos cujo `agent_ids` inclui aquele agente. Renderiza 1 nó expansível por agente (cabeçalho: nome + contagem + botão "+ Enviar arquivo" escopado; corpo: lista de arquivos com status + as 3 ações — anexar a outro agente, desanexar deste, excluir). Parâmetro `?agent_id=` na URL abre a página com a pasta daquele agente já expandida (em vez de pré-selecionar um upload).
- **`AgentDetail.tsx`**: remove a UI de attach/detach e o link de upload direto, mas mantém a chamada já existente a `GET /agents/{id}/knowledge-base-files` (só pra saber o total). Mostra `"{N} arquivo(s) anexado(s)"` com um link `<Link href={`/base-de-conhecimento?agent_id=${agent.id}`}>`.

## Fora de escopo

- Qualquer mudança no modelo de dados (`agent_knowledge_base_files` continua exatamente como está — só o comportamento da API de detach muda, não o schema).
- Drag-and-drop entre pastas — a única forma de mover/compartilhar um arquivo é a ação explícita "anexar a outro agente".
- Filtro/busca de arquivos dentro da árvore, ordenação configurável — fica pra uma iteração futura se necessário.
- Mudança em `POST /knowledge-base/files` (upload) alem de garantir que o botão de cada pasta sempre manda `agent_id` explícito — o comportamento de fallback pro ponto de entrada continua existindo no backend (nunca deveria ser exercitado pela UI nova, mas não é removido, pra não quebrar nenhum outro consumidor hipotético do endpoint).

## Testes

- Backend: teste novo pra `GET /knowledge-base/files` confirmando `agent_ids` correto (arquivo em 0, 1 e 2+ agentes). Teste novo pra `DELETE /agents/{id}/knowledge-base-files/{file_id}` recusando com `409` quando é o último vínculo, e permitindo quando há mais de um.
- Frontend: `KnowledgeBasePanel.tsx` — árvore renderiza 1 nó por agente (incluindo vazio), arquivo em 2 agentes aparece nos 2 nós, upload por pasta manda o `agent_id` certo, `?agent_id=` expande a pasta certa, ação de anexar/desanexar/excluir funcionam. `AgentDetail.tsx` — mostra a contagem certa e o link aponta pro `agent_id` certo.
