# Árvore da base de conhecimento e pacotes por especialidade

Como a base de conhecimento de cada agente é organizada, o que muda no
atendimento e como carregar os documentos que a Advoxs entrega prontos.

## A árvore

Cada agente tem as mesmas pastas, na mesma ordem:

| Pasta | Conteúdo | Quem preenche |
|---|---|---|
| A) Livros digitais | livros | escritório |
| B) Artigos científicos | artigos | escritório |
| C) Processos judiciais | processos | escritório |
| D) Matérias jornalísticas | matérias | escritório |
| E) Modelos | lista de modelos; subpastas Modelos de contratos e Peças processuais | Advoxs (lista) e escritório |
| F) Não selecionáveis | arquivos guardados que o agente **não lê** | escritório |
| Z) Constituição | Constituição Federal | Advoxs |
| Z › 1) Legislação específica | leis da especialidade | Advoxs |
| Z › 2) Decisões de tribunais | súmulas, teses e repetitivos | Advoxs |
| Z › 3) a 6) | pastas da área (ex.: convenção, regimento, atas e comunicados no Condominial) | escritório |

- A pasta de um arquivo é o campo `category` (`knowledge_base_files`). Os valores
  estão em `apps/api/app/core/knowledge_tree.py` e em
  `apps/web/src/lib/kb-categories.ts`.
- As pastas A–F e Z são fixas. As pastas 3 a 6 só existem nas especialidades que as
  definem (Cível, Consumidor, Empresarial, Condominial e Contratos) e mudam de nome
  conforme a especialidade do agente.
- **Só a pasta F muda o atendimento:** o agente não consulta os arquivos dela. As
  demais organizam.

## Especialidade do agente

`agents.specialty` guarda uma das 16 especialidades (ou nada). O escritório escolhe
em `/base-de-conhecimento`, dentro da pasta do agente. A especialidade define os
nomes das pastas 3 a 6 e qual pacote de documentos o agente recebe.

- `PUT /api/v1/agents/{id}/specialty` (`{"specialty": "contratos"}` ou `null`).
- Trocar de especialidade tira do agente as cópias do pacote anterior. As que não
  estiverem em outro agente são excluídas. Os documentos do próprio escritório não
  mudam.
- Os agentes padrão Condominial, Contratos e Direito do Consumidor já nascem com a
  especialidade. Nos escritórios existentes, a migration `0053` preenche os que
  ainda têm o nome original.

## Pacotes

A Advoxs mantém uma base central (`public_documents`) com a legislação e a
jurisprudência oficiais. Cada documento é lido **uma vez** pelo `api_rag`, no tenant
reservado `advoxs-public`. `specialty_package_documents` diz quais documentos formam
o pacote de cada especialidade.

Quando um agente recebe uma especialidade, o escritório ganha **uma cópia** de cada
documento do pacote:

- é um arquivo normal do escritório (`knowledge_base_files` com
  `public_document_id`), nas pastas E, Z, Z.1 e Z.2, com o selo "base Advoxs";
- o escritório pode excluir, mudar de pasta ou anexar a outro agente;
- **não conta** no limite de arquivos nem de espaço do plano;
- a cópia duplica os trechos já indexados (`POST /documents/users/copy` no
  `api_rag`), sem ler o arquivo de novo, e roda na fila de ingestão
  (`copy_public_document`);
- dois agentes do mesmo escritório com a mesma lei usam o mesmo arquivo.

**Restaurar pacote** (`POST /api/v1/agents/{id}/package/restore`, botão no painel)
traz de volta os documentos excluídos e atualiza os que mudaram na base central.
Uma atualização da central **não** chega sozinha aos escritórios: cada um decide
quando restaurar.

### Como o agente busca

A legislação do pacote é buscada à parte dos documentos do escritório. Numa busca
só, as leis (milhares de páginas) tomariam o lugar do contrato do cliente nos
resultados. O `worker` e o `api` enviam ao `agents`, por agente,
`knowledge_base_file_ids` (tudo o que ele pode ler) e `reference_file_ids` (as
cópias de pacote). `buscar_base_conhecimento_agente` faz as duas buscas ao mesmo
tempo e devolve primeiro os trechos do escritório.

## Carregar a base central

Os documentos ficam na pasta `BASE-PUBLICA` do Drive da Advoxs. O script lê a
subpasta `PACOTES` (uma pasta por especialidade, `01_CIVEL` a `16_AMBIENTAL`, cada
uma com a árvore A–F e Z) e considera só as pastas que a Advoxs preenche: E, Z, Z.1
e Z.2. O mesmo documento em vários pacotes é lido uma vez só (a identidade é o
conteúdo, SHA-256).

1. Baixe a pasta `BASE-PUBLICA` do Drive, copie para o servidor e de lá para dentro
   do contêiner do `api`:

   ```bash
   docker compose cp /caminho/BASE-PUBLICA api:/tmp/base
   ```

2. Confira o que será carregado, sem gravar nada:

   ```bash
   docker compose exec api uv run python scripts/load_public_base.py /tmp/base --dry-run
   ```

3. Carregue. A primeira carga lê cerca de 16 mil páginas e leva algumas horas; o
   script mostra o andamento e pode ser interrompido e rodado de novo:

   ```bash
   docker compose exec api uv run python scripts/load_public_base.py /tmp/base --provision
   ```

   `--provision` entrega os pacotes aos agentes que já têm especialidade. Sem ela,
   cada escritório recebe o pacote ao escolher a especialidade ou ao restaurar.

Use `docker compose exec` (contêiner que já está no ar), não `docker compose run`.
A pasta copiada some no próximo deploy, que recria os contêineres.

Rodar de novo depois de atualizar a pasta lê só os documentos que mudaram e regrava
a composição dos pacotes.

## Limites conhecidos

- O painel lista até 500 arquivos por escritório.
- O espaço do índice de busca cresce com cada escritório que recebe pacotes.
- Uma cópia que ficou "processando" (fila interrompida) volta com "Restaurar pacote".
