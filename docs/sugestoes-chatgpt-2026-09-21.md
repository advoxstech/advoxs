# Advoxs — sugestões do ChatGPT após a reunião de 21/09/2026

## Origem e status

Estas mudanças foram **sugeridas pelo ChatGPT após a reunião do dia 21/09/2026 (21/09/26)** e registradas no projeto a pedido do usuário.

Fontes: documento **ANÁLISE CHATGPT - advoxs.pdf** fornecido pelo usuário e esclarecimentos na conversa, especialmente a orientação de preservar a identidade visual existente. O PDF contém melhorias das funcionalidades atuais, sua ordem de prioridade, sugestões de frontend e novas funcionalidades.

**Status: backlog em avaliação e implementação incremental.** Os itens concluídos ou temporariamente desativados estão identificados na tabela. Os demais continuam como sugestões, sem indicar aprovação para desenvolvimento ou validação em produção. A análise original foi baseada no código do repositório; as sugestões de interface não resultaram de uma inspeção visual das telas no navegador.

## Diretriz obrigatória: preservar a identidade visual

**Manter as cores e a identidade visual atuais. O design foi definido intencionalmente e deve ser respeitado.** As sugestões de frontend abaixo devem ser adaptadas ao padrão existente, com foco em funcionamento, responsividade, clareza e acessibilidade, sem propor uma nova identidade visual.

Referências a hierarquia, legibilidade, contraste e organização não autorizam substituir a paleta, a tipografia ou os demais elementos da identidade. Ajustes devem aproveitar os componentes e estilos existentes. Propostas de alteração estrutural de telas continuam sendo sugestões a avaliar, não um redesign já aprovado.

## 1. Melhorias do que já está implementado

A ordem abaixo reproduz a priorização sugerida na conversa e no PDF, considerando segurança, perda de mensagens, impacto financeiro e operação diária.

| Ordem | Prioridade | Área | Mudança sugerida |
| --- | --- | --- | --- |
| 1 | Crítica — **Implementada em 22/09/2026** | Segurança dos uploads | Os uploads de base de conhecimento e logo validam o conteúdo real do formato aceito, usam caminhos resolvidos dentro dos diretórios permitidos e gravam de forma atômica. Nomes recebidos são normalizados para exibição, enquanto o armazenamento físico usa apenas identificadores internos. |
| 2 | Crítica — **Implementação temporariamente desativada em 21/09/2026** | Configuração de produção | A validação completa permanece no código, mas não bloqueia deploys até que os segredos possam ser configurados no servidor. Após preencher o `.env`, defina `ENFORCE_PRODUCTION_CONFIG=true` para reativá-la. Veja [configuração de produção](configuracao-producao.md). |
| 3 | Crítica — **Implementada em 22/09/2026** | Recebimento de mensagens | Cada mensagem recebida passa a criar, na mesma transação, uma pendência durável de processamento. Se a fila estiver indisponível, o webhook confirma o recebimento e o worker tenta reenfileirar periodicamente; trabalhos duplicados ou abandonados são reservados de forma atômica antes de executar. |
| 4 | Crítica | Processamento e envio | Impedir respostas e cobranças duplicadas nas novas tentativas. Quando somente a entrega falhar, reenviar a resposta pronta sem executar a IA novamente; recuperar separadamente geração, persistência, envio e cobrança. |
| 5 | Crítica | Ordem das conversas | Evitar execuções simultâneas da IA para o mesmo contato, preservando a sequência das mensagens e a consistência do contexto. |
| 6 | Crítica — **Implementada em 22/09/2026** | Débito de créditos | O saldo é travado durante o débito concorrente e a cobrança fica limitada ao valor disponível, sem permitir saldo negativo. O custo não coberto é registrado separadamente para auditoria e bloqueio dos turnos seguintes. |
| 7 | Alta — **Implementada em 22/09/2026** | Consumo das assinaturas | Execuções cobertas por assinatura registram tokens e custo operacional real sem gerar débito de créditos, separando uso do serviço e cobrança. |
| 8 | Alta | Atendimento humano | Impedir que a IA reassuma apenas porque o atendente fechou a aba ou perdeu conexão. Conferir novamente o estado da conversa antes do envio automático. |
| 9 | Alta | Autenticação | Tornar a renovação de sessão atômica, revogar sessões após troca de senha e limitar tentativas de login. |
| 10 | Alta — **Implementada em 21/09/2026** | Indexação dos arquivos | O serviço de RAG agora interrompe a ingestão quando o Qdrant não confirma a gravação. O worker só marca o documento como pronto e remove o arquivo temporário após o sucesso; falhas temporárias são repetidas e, após a última tentativa, o arquivo fica preservado com status de erro para reprocessamento. |
| 11 | Alta | Busca na base de conhecimento | Diferenciar ausência de informação de indisponibilidade do serviço, evitando que uma falha técnica seja tratada como busca sem resultados. |
| 12 | Alta | Limites de arquivos | Aplicar limites durante a leitura e o download, interrompendo arquivos grandes antes de carregá-los inteiramente na memória. |
| 13 | Alta | Documentos gerados | Proteger o armazenamento, usar links temporários para entrega e melhorar a recuperação entre geração, registro e envio do PDF. |
| 14 | Alta | Exclusão de conversas | Garantir a limpeza dos anexos e da memória dos agentes com novas tentativas automáticas quando serviços externos estiverem indisponíveis. |
| 15 | Alta | Logs | Reduzir o registro de mensagens e dados sensíveis, preservando identificadores e informações suficientes para investigar falhas. |
| 16 | Alta | Testes automatizados | Incluir o RAG e ampliar testes de integração com banco e fila, principalmente isolamento entre escritórios, pagamentos repetidos e recuperação de falhas. |
| 17 | Alta | Implantação | Reduzir a indisponibilidade causada pela parada de todos os serviços, fixar versões das imagens e verificar a saúde da aplicação após cada deploy. |
| 18 | Alta — **Implementada em 22/09/2026** | Relatórios financeiros | Cada execução registra custo real, valor cobrado, eventual falta de saldo, origem do pagamento, tokens e custo de documentos. O painel consolida esses dados, e compras novas preservam o preço pago para que mudanças no pacote não alterem o histórico. Registros anteriores à migração usam o preço atual como fallback porque o valor histórico não existia. |
| 19 | Média | Histórico de conversas | Usar a paginação existente na API para acessar conversas e mensagens anteriores às 50 mais recentes. |
| 20 | Média — **Implementada em 21/09/2026** | Estados do atendimento | O backend agora mantém separadamente o responsável pelo atendimento (`agent`, `human` ou `billing_gate`) e a situação operacional da automação (`idle`, `processing` ou `failed`). A API consolida esses dados em um status único, e a lista, o atendimento e o ambiente de teste usam o mesmo componente para mostrar “IA disponível”, “IA processando”, “Atendimento humano”, “Aguardando pagamento” ou “Falha no atendimento”. As transições limpam dados antigos do billing gate e falhas definitivas deixam de aparecer como processamento ativo. |
| 21 | Média | Atualização do painel | Sinalizar falhas de rede e dados desatualizados, evitando que erros apareçam como listas vazias. |
| 22 | Média | Experiência em celular | Adaptar menu, lista de conversas e histórico a telas pequenas, evitando colunas fixas que comprimam o atendimento. |
| 23 | Média | Onboarding | Fazer o tutorial refletir o estado real da configuração e indicar o que falta para o primeiro atendimento funcionar. |
| 24 | Média — **Implementada em 21/09/2026** | Documentação | A visão geral e os contratos técnicos foram atualizados conforme o código atual; funcionalidades implementadas, parciais e planejadas agora estão identificadas, e specs/planos antigos foram classificados como registros históricos. |

**Testes e documentação específicos devem acompanhar cada mudança.** A posição 16 representa a ampliação geral da cobertura, não uma orientação para adiar a validação das correções anteriores. O item 24 concluiu a revisão geral em 21/09/2026; a documentação deve continuar sendo atualizada a cada mudança futura.

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
