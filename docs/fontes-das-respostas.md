# Fontes das respostas

Implementada em 24/09/2026, por sugestão do ChatGPT e aprovação do usuário.

## Para o escritório

As mensagens de texto dos agentes exibem uma seção recolhível **Fontes da resposta** nas
conversas reais e nas conversas de teste, incluindo testes de rascunho. Ela mostra documento,
trecho e página, quando disponível, e permite baixar o documento original mediante autenticação.
Não altera cores, fontes ou identidade visual. As referências internas não são anexadas ao
texto entregue pelo WhatsApp.

| Situação | Informação exibida |
| --- | --- |
| Referências válidas | Documentos e trechos indicados pelo agente e verificados na consulta. |
| Sem documentos vinculados | Resposta sem consulta à base do escritório. |
| Busca não acionada | Base não consultada nesta resposta. |
| Busca vazia | Nenhum conteúdo encontrado na consulta. |
| Resultados não referenciados | Resposta sem referências da base. |
| Falha técnica | Base temporariamente indisponível durante esta resposta. |
| Registro antigo/sem metadados | Fontes não registradas para esta mensagem. |

O uso de conhecimento nativo continua permitido. Uma busca vazia não bloqueia o atendimento.
Quando uma resposta possui fontes e outra consulta falhou, o painel também informa essa falha.

## Como funciona

1. A busca do agente devolve um resultado estruturado, distinguindo ausência de documentos,
   ausência de resultados e indisponibilidade.
2. O serviço de agentes verifica tenant, base `kb` e vínculo do documento ao agente antes
   de expor o trecho. Cada trecho recebe um identificador interno temporário.
3. O agente finaliza por `responder_com_fontes`, separando `answer` de `reference_ids`.
   Somente referências efetivamente indicadas e presentes na consulta autorizada são aceitas.
   A finalização aproveita a chamada do modelo; não executa uma segunda IA para atribuir fontes.
4. As referências são verificadas por agente e por execução. A mudança de turno limpa os
   candidatos anteriores. Uma transferência não atribui automaticamente fontes de outro agente.
5. O retorno interno mantém `responses` e acrescenta `response_sources`, alinhado por índice.
   O worker e o serviço de testes persistem a evidência na mesma mensagem/transação da resposta.
6. O reenvio usa a mensagem já persistida e não consulta novamente a base. Cancelamentos por
   nova mensagem ou atendimento humano continuam seguindo o fluxo existente.

`messages.response_sources` é um campo JSONB opcional, protegido pelo isolamento existente
de mensagens por escritório. Contém `status`, `sources` e `search_failed`; cada fonte contém
`document_id`, `filename`, `chunk_id`, `excerpt` e `page`. Não guarda URLs públicas nem tokens.
Mensagens anteriores permanecem com valor nulo, sem reconstrução retroativa de evidências.
A exclusão da mensagem também elimina sua evidência, sem criar registros órfãos.

## Originais e histórico

O download segue navegador → proxy autenticado → API → serviço RAG. A API verifica o escritório;
o RAG exige autenticação interna, confirma tenant/base e restringe o caminho ao diretório
autorizado. O download é limitado ao tamanho máximo de arquivo já configurado e não é armazenado
em cache compartilhado. Nenhuma rota pública de acesso aos documentos foi criada.

O trecho registrado representa o conteúdo consultado naquele momento. Se o documento for
excluído, o trecho continua no histórico da mensagem, mas a tentativa de baixar o original
informa sua indisponibilidade. Falhas de conexão oferecem nova tentativa.

## Limitações da primeira versão

- Escopo: base de conhecimento vinculada aos agentes. Anexos pessoais dos clientes e bases
  gerais do sistema não têm atribuição de fontes nesta entrega.
- A extração atual não preserva páginas dos PDFs. O campo é opcional, sem inferir páginas nem
  exigir reindexação do acervo existente.
- Até oito referências por resposta; cada trecho disponibilizado à IA tem até 3.000 caracteres.
- Documento recuperado não significa documento utilizado. As fontes refletem a indicação do
  agente validada contra a consulta, não uma garantia da interpretação ou da correção jurídica.
- Uma resposta normal de texto que não indique referências é exibida sem fontes atribuídas;
  não há tentativa de adivinhar sua origem.
- A evidência adiciona armazenamento e pode aumentar os tokens da chamada do agente.

## Deploy e validação

A migração **0039** adiciona apenas o campo opcional. O deploy já executa `alembic upgrade head`.
Não há novas variáveis no `.env`, serviços, volumes ou configuração manual da VPS. API, worker,
agentes, RAG e frontend precisam receber as novas imagens para disponibilizar o fluxo completo.

Os testes cobrem escopo de documentos, referências inválidas, transferência, renovação do
turno, resultados da busca, separação do envio ao WhatsApp, persistência por mensagem, download
autenticado, original excluído, falha de rede e compatibilidade com mensagens antigas.
O teste PostgreSQL verifica migração, isolamento por tenant, preservação nos estados de entrega,
exclusão da evidência e downgrade sem remover o conteúdo das mensagens.


Validação local desta entrega: 647 testes da API, 188 dos agentes, 179 do worker,
52 do RAG, 308 do frontend e 3 testes de integração PostgreSQL (incluindo a migração
nova e os testes existentes de versões). Lint, formatação Python e build do frontend
verificados. Interface exercitada em navegador a 1440 e 390 pixels, nas conversas
reais e de teste, com serviços e respostas simulados. Não houve chamada paga à IA
nem alteração no ambiente de produção durante essas verificações.
