# Advoxs — sugestões do ChatGPT após a reunião de 21/09/2026

## Origem e status

Estas mudanças foram **sugeridas pelo ChatGPT após a reunião do dia 21/09/2026 (21/09/26)** e registradas no projeto a pedido do usuário.

Fontes: documento **ANÁLISE CHATGPT - advoxs.pdf** fornecido pelo usuário e esclarecimentos na conversa, especialmente a orientação de preservar a identidade visual existente. O PDF contém melhorias das funcionalidades atuais, sua ordem de prioridade, sugestões de frontend e novas funcionalidades.

**Status: sugestões registradas, pendentes de avaliação e implementação.** Este documento não significa que as mudanças foram executadas, que todas foram aprovadas para desenvolvimento ou que seus resultados foram validados em produção. A análise original foi baseada no código do repositório; as sugestões de interface não resultaram de uma inspeção visual das telas no navegador.

## Diretriz obrigatória: preservar a identidade visual

**Manter as cores e a identidade visual atuais. O design foi definido intencionalmente e deve ser respeitado.** As sugestões de frontend abaixo devem ser adaptadas ao padrão existente, com foco em funcionamento, responsividade, clareza e acessibilidade, sem propor uma nova identidade visual.

Referências a hierarquia, legibilidade, contraste e organização não autorizam substituir a paleta, a tipografia ou os demais elementos da identidade. Ajustes devem aproveitar os componentes e estilos existentes. Propostas de alteração estrutural de telas continuam sendo sugestões a avaliar, não um redesign já aprovado.

## 1. Melhorias do que já está implementado

A ordem abaixo reproduz a priorização sugerida na conversa e no PDF, considerando segurança, perda de mensagens, impacto financeiro e operação diária.

| Ordem | Prioridade | Área | Mudança sugerida |
| --- | --- | --- | --- |
| 1 | Crítica | Segurança dos uploads | Substituir o nome físico recebido por um identificador seguro; validar o conteúdo e o caminho final de gravação, impedindo escrita fora do diretório permitido. |
| 2 | Crítica | Configuração de produção | Impedir a inicialização sem os segredos obrigatórios de autenticação interna e validação de webhooks, evitando funcionamento permissivo por configuração incompleta. |
| 3 | Crítica | Recebimento de mensagens | Garantir que mensagens salvas sejam processadas mesmo quando o enfileiramento falhar, com registro durável de pendências e recuperação automática. |
| 4 | Crítica | Processamento e envio | Impedir respostas e cobranças duplicadas nas novas tentativas. Quando somente a entrega falhar, reenviar a resposta pronta sem executar a IA novamente; recuperar separadamente geração, persistência, envio e cobrança. |
| 5 | Crítica | Ordem das conversas | Evitar execuções simultâneas da IA para o mesmo contato, preservando a sequência das mensagens e a consistência do contexto. |
| 6 | Crítica | Débito de créditos | Controlar o saldo em execuções concorrentes e definir uma política explícita para impedir ou limitar consumo além do saldo disponível. |
| 7 | Alta | Consumo das assinaturas | Registrar tokens e custo real mesmo quando o uso estiver incluído na assinatura, separando consumo operacional de cobrança por mensagem. |
| 8 | Alta | Atendimento humano | Impedir que a IA reassuma apenas porque o atendente fechou a aba ou perdeu conexão. Conferir novamente o estado da conversa antes do envio automático. |
| 9 | Alta | Autenticação | Tornar a renovação de sessão atômica, revogar sessões após troca de senha e limitar tentativas de login. |
| 10 | Alta | Indexação dos arquivos | Marcar um documento como pronto somente após confirmar a indexação; preservar o arquivo para reprocessamento quando houver falha. |
| 11 | Alta | Busca na base de conhecimento | Diferenciar ausência de informação de indisponibilidade do serviço, evitando que uma falha técnica seja tratada como busca sem resultados. |
| 12 | Alta | Limites de arquivos | Aplicar limites durante a leitura e o download, interrompendo arquivos grandes antes de carregá-los inteiramente na memória. |
| 13 | Alta | Documentos gerados | Proteger o armazenamento, usar links temporários para entrega e melhorar a recuperação entre geração, registro e envio do PDF. |
| 14 | Alta | Exclusão de conversas | Garantir a limpeza dos anexos e da memória dos agentes com novas tentativas automáticas quando serviços externos estiverem indisponíveis. |
| 15 | Alta | Logs | Reduzir o registro de mensagens e dados sensíveis, preservando identificadores e informações suficientes para investigar falhas. |
| 16 | Alta | Testes automatizados | Incluir o RAG e ampliar testes de integração com banco e fila, principalmente isolamento entre escritórios, pagamentos repetidos e recuperação de falhas. |
| 17 | Alta | Implantação | Reduzir a indisponibilidade causada pela parada de todos os serviços, fixar versões das imagens e verificar a saúde da aplicação após cada deploy. |
| 18 | Alta | Relatórios financeiros | Consolidar os diferentes caminhos de consumo e custos de busca, embeddings e geração de documentos; registrar o preço efetivo de cada compra sem recalcular o passado pelo preço atual do pacote. |
| 19 | Média | Histórico de conversas | Usar a paginação existente na API para acessar conversas e mensagens anteriores às 50 mais recentes. |
| 20 | Média | Estados do atendimento | Representar corretamente IA, atendimento humano, espera por pagamento e falhas; evitar mostrar agente respondendo quando a conversa aguarda pagamento. |
| 21 | Média | Atualização do painel | Sinalizar falhas de rede e dados desatualizados, evitando que erros apareçam como listas vazias. |
| 22 | Média | Experiência em celular | Adaptar menu, lista de conversas e histórico a telas pequenas, evitando colunas fixas que comprimam o atendimento. |
| 23 | Média | Onboarding | Fazer o tutorial refletir o estado real da configuração e indicar o que falta para o primeiro atendimento funcionar. |
| 24 | Média | Documentação | Atualizar descrições antigas ou substituídas e distinguir comportamento atual de funcionalidades parcialmente implementadas. |

**Testes e documentação específicos devem acompanhar cada mudança.** As posições 16 e 24 representam a ampliação geral dessas frentes, não uma orientação para adiar a validação ou a documentação das correções anteriores. O registro deste backlog não conclui o item 24.

## 2. Sugestões de frontend e usabilidade

A prioridade desta tabela é interna à frente de frontend; não substitui a ordem geral da seção anterior. Há sobreposição intencional com melhorias existentes: são detalhamentos, não tarefas duplicadas.

Todas as propostas devem respeitar a diretriz de preservação da identidade visual.

| Prioridade | Área | Mudança sugerida |
| --- | --- | --- |
| 1 | Tela de conversas | Dar mais espaço ao histórico e ao campo de resposta; avaliar um painel recolhível para resumo, informações do cliente e cobrança, dentro do design existente. |
| 2 | Responsividade | No celular, mostrar lista e conversa em telas separadas, com botão de voltar, e adaptar a navegação lateral para uma apresentação compacta. |
| 3 | Estado do atendimento | Exibir IA atendendo, atendimento humano, aguardando pagamento e falha no envio com texto e ícone, sem depender somente da cor. |
| 4 | Controle IA/humano | Avaliar ações explícitas de assumir atendimento e devolver à IA, mantendo o estado visível junto ao campo de resposta e respeitando os componentes atuais. |
| 5 | Lista de conversas | Destacar nome, quando disponível, ou telefone, prévia da última mensagem e horário; deixar saldo e informações financeiras em segundo plano. |
| 6 | Menu de navegação | Evitar depender exclusivamente do mouse para revelar nomes; preservar a preferência de menu aberto ou fechado e melhorar o acesso por teclado. |
| 7 | Dashboard | Destacar situações que exigem ação, como atendimento pendente, WhatsApp desconectado, saldo baixo e arquivo com erro, antes das métricas e gráficos. |
| 8 | Organização financeira | Diferenciar melhor créditos do escritório de cobrança dos clientes, esclarecendo quem paga quem. |
| 9 | Base de conhecimento | Evidenciar os arquivos de cada agente, o processamento e a ação de tentar novamente; apresentar erros compreensíveis. |
| 10 | Edição de agentes | Organizar nome, instruções e arquivos vinculados; melhorar a área de edição e indicar alterações ainda não salvas. |
| 11 | Feedback das ações | Padronizar carregamento, sucesso, erro e nova tentativa, distinguindo falha de conexão de ausência de dados. |
| 12 | Legibilidade e acessibilidade | Revisar a leitura dos textos, foco por teclado e tamanho das áreas clicáveis; avaliar contraste usando a paleta existente, preservando tipografia e identidade. |

Complementos discutidos no chat:

- Substituir confirmações nativas do navegador por modais consistentes com a interface, explicando consequências e usando ações específicas, como excluir histórico.
- Evitar que novas mensagens desloquem automaticamente quem está lendo um trecho antigo; oferecer uma ação para voltar às novas mensagens.
- Priorizar, nesta frente, a tela de conversas, a experiência no celular e a clareza dos estados de atendimento.

## 3. Novas funcionalidades sugeridas

Esta seção registra as propostas adicionais presentes no PDF. São possibilidades futuras, separadas das correções do que já existe. **Não foi definida uma ordem individual de implementação para estas oito propostas.**

| Funcionalidade | Proposta | Benefício esperado |
| --- | --- | --- |
| Equipe e permissões | Convites, perfis de administrador, advogado, atendente e financeiro, com responsáveis por conversa. | Permitir trabalho em equipe sem compartilhamento de credenciais. |
| CRM jurídico enxuto | Cadastro do contato, assunto, área jurídica, etapa, responsável, anotações e próxima ação. | Transformar atendimento em acompanhamento comercial e operacional. |
| Áudio e OCR | Transcrição de áudios e leitura de imagens e PDFs digitalizados. | Ampliar o atendimento além dos documentos com texto extraível. |
| Revisão humana de documentos | Gerar rascunho, editar, aprovar e então enviar, mantendo versões e histórico. | Dar mais controle sobre os documentos produzidos pela IA. |
| Fontes das respostas | Mostrar documento, trecho e, quando disponível, página usados pela IA. | Facilitar conferência das respostas e correção da base de conhecimento. |
| Versões dos agentes | Rascunho, teste, publicação, comparação e retorno à versão anterior. | Evitar que mudanças nas instruções afetem imediatamente todos os atendimentos. |
| Agenda e acompanhamento | Agendamento de reuniões, lembretes e tarefas associadas ao contato. | Converter conversas em ações concretas do escritório. |
| Central de operação | Alertas de WhatsApp desconectado, mensagem não entregue, fila parada, arquivo com erro e saldo crítico. | Permitir agir antes que o cliente reclame. |

O benefício da central de operação foi recuperado da conversa original porque a última frase do PDF está truncada.

## 4. Orientação para execução futura

- Começar pelas melhorias críticas da seção 1, seguidas das de prioridade alta.
- Validar no código vigente o comportamento afetado antes de implementar cada sugestão, pois o projeto pode evoluir após este registro.
- Tratar os detalhamentos de frontend junto aos itens correspondentes do backlog, evitando duplicidade de trabalho.
- Preservar as cores, a identidade visual e o padrão de design já definidos.
- Atualizar o status de cada proposta somente após implementação e verificação; a inclusão neste documento não equivale à conclusão.
