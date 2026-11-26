# Precisão jurídica e atendimento humano

Implementação local de 23/11/2026. Requer migração **0057**, API, worker,
motor dos agentes e interface atualizados juntos.

## Comportamento

- Pedidos diretos de atendimento humano são registrados na inserção da mensagem,
  mesmo se o modelo estiver indisponível. Pedidos contextuais e necessidade de
  revisão jurídica também podem ser registrados pela ferramenta do agente.
- A solicitação fica separada da urgência. Há data, motivo, agente responsável,
  episódio para evitar duplicidade e evento no histórico do atendimento.
- O agente atual permanece vinculado ao pedido. A solicitação não transfere para
  a recepção nem pausa automaticamente a IA. A equipe pausa ao assumir o atendimento.
- O painel mostra a pendência e permite filtrar por “Solicitou humano”.
- A outbox notifica advogados ativos vinculados ao agente; sem eles, administradores
  ativos. O destinatário e suas permissões são revalidados antes do envio. Conversas
  de teste não enviam e-mails. Tentativas e recuperação continuam na outbox existente.
- O agente informa o registro do pedido, sem prometer entrega do e-mail ou horário
  de resposta. Enquanto aguarda, oferece apoio factual breve.

## Precisão e limites

As instruções comuns passam a diferenciar fatos documentais de conclusões jurídicas.
Ausência de fontes ou falha na busca exige revisão humana para conclusões. O motor
bloqueia padrões de afirmações normativas sem referência legal atribuída e uma
regressão conhecida de condicionamento comercial do frete/reembolso no arrependimento.
Arquivos de legislação/jurisprudência são identificados pelas categorias
`constituicao`, `legislacao_especifica` e `decisoes_tribunais`; a categoria **não é
uma certificação da qualidade ou atualidade do conteúdo**.

Este filtro é conservador e baseado em padrões: pode encaminhar respostas que
seriam corretas e não detecta todo erro jurídico. A existência de uma fonte também
não comprova a interpretação. Não equivale à validação jurídica pelo escritório.

As instruções incentivam respostas curtas, uma pergunta essencial por vez,
continuidade do histórico e documentos em PDF/DOCX/TXT. A limitação de arquivos com
senha é explicada como limitação técnica, sem atribuí-la à LGPD.

## Referências para validação pelo advogado

- CDC, art. 49: https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm
- STJ, REsp 1.340.604/RJ: https://scon.stj.jus.br/SCON/pesquisar.jsp?O=JT&b=ACOR&livre=1340604.num.

Cenários a homologar: compra a distância com pedido de arrependimento; data de
recebimento desconhecida; vício/defeito versus arrependimento; empresa como
fornecedora; fonte conflitante; cláusula contratual versus direito aplicável.

O exemplo documental permanece estritamente factual: carência de 73 dias,
mensalidade de R$ 2.470, vencimento no dia 12, aviso de 45 dias e foro ausente.
Esses valores fictícios não são parâmetros de produção.

## Publicação e homologação pendentes

1. Revisão dos cenários e das referências pelo advogado do escritório.
2. Conferência da atualidade e classificação das fontes da base; esta alteração
   não reescreve nem remove documentos do escritório automaticamente.
3. Publicação dos serviços e aplicação de `alembic upgrade head` com o papel de
   migrações, no ambiente correspondente. A migração foi verificada em banco descartável.
4. Repetir os seis cenários no ambiente publicado, inclusive anexo PDF, ordem
   transferência → apresentação → resposta, pedido humano, recebimento de e-mail
   e clique em Assumir atendimento. Os testes automatizados usam modelo e SMTP
   simulados; não comprovam comportamento de um modelo real nem entrega de e-mail.

## Verificação técnica local

- API: 909 testes passaram; equipe/múltiplos escritórios: 12 testes em PostgreSQL.
- Motor: 269 testes passaram, incluindo bloqueio sem fonte e ordem da transferência.
- Worker: 369 testes existentes e 2 de atualização do contexto passaram; 18 testes
  em PostgreSQL cobrem a outbox existente e os pedidos humanos.
- Interface: 69 testes existentes e 3 novos casos passaram.
- Migrações do zero até 0057, reversão para 0056 e reaplicação passaram em banco
  descartável. Inserção pelo papel `advoxs_app`, auditoria, outbox e isolamento por
  escritório foram conferidos nesse banco com o esquema completo.
- Lint e tipagem dos fontes da interface passaram. A checagem TypeScript global
  ainda aponta erros anteriores nos arquivos de testes web; não foram alterados
  por esta implementação.

## Identidade do escritório e confirmação do PDF — 24/11/2026

Os seis prompts atuais da Helder Goes foram auditados: nenhum continha GVA.
Os prompts padrão de provisionamento ainda continham esse nome fixo; foram
neutralizados no código. O episódio relatado não foi reproduzido nos testes
atuais, portanto a origem na memória de uma conversa antiga é uma hipótese,
não uma causa comprovada. Históricos existentes não foram apagados.

API e worker passam ao motor o nome do escritório obtido do cadastro do tenant.
Esse dado prevalece sobre exemplos e identidades antigas presentes no histórico.
Não há migração nova. Essa proteção do motor exige publicar os serviços.

Após geração e armazenamento bem-sucedidos, o motor encerra o turno com uma
confirmação automática, sem chamar novamente o modelo para decidir se deve enviar
o PDF ou oferecer outro canal. A confirmação descreve geração e anexação pela
plataforma, sem afirmar que o destinatário recebeu ou abriu o documento.
Falhas de geração continuam no fluxo de erro, e a confirmação de um PDF antigo
não é reutilizada em um novo turno.

Um teste real revelou escolha indevida de advertência para um pedido de comunicado.
O prompt passou a mapear explicitamente os tipos e o motor bloqueia a ferramenta
incompatível quando há pedido de geração explícito e inequívoco no turno atual.
Pedidos ambíguos continuam dependendo de esclarecimento e das instruções do agente.

Os seis prompts da Helder Goes receberam a versão 3 publicada. Foram testados:
identidade dos seis agentes sob sugestão de GVA; saudação simples; contestação
baseada em apresentação antiga; e geração de comunicado com pedido natural.
A rodada final anexou Aviso.pdf e respondeu apenas que o arquivo acompanhava
a mensagem, sem pergunta ou oferta adicional. Os testes foram isolados,
sem mensagens enviadas ao WhatsApp nem e-mails à equipe.

Validação local do código: 286 testes do motor, 911 da API e 372 do worker
passaram. Integrações que dependem de PostgreSQL não configurado foram puladas.
O lint dos arquivos alterados passou. Os prompts estão publicados. As proteções
do motor exigem publicar API, worker e agentes; não exigem migração adicional.
