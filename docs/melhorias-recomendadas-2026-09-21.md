# Melhorias recomendadas — resumo executivo

Documento curto com melhorias selecionadas a partir da análise do projeto e
das sugestões registradas após a reunião de 21/09/2026. São recomendações: a
inclusão aqui não significa que foram aprovadas ou implementadas.

## 10 melhorias no que já existe

| Ordem | Melhoria | Resultado esperado |
| --- | --- | --- |
| 1 | Recuperar mensagens que foram salvas, mas não chegaram à fila. | Nenhum contato fica sem atendimento por uma falha temporária de enfileiramento. |
| 2 | Separar as tentativas de gerar, cobrar, persistir e entregar uma resposta. | Evita repetir a IA ou cobrar novamente quando apenas o envio falhar. |
| 3 | Revalidar o estado do atendimento imediatamente antes do envio automático. | A IA não retoma uma conversa que foi assumida por uma pessoa. |
| 4 | Diferenciar “não encontrei essa informação” de falha na base de conhecimento. | O cliente recebe uma resposta correta e o escritório consegue identificar indisponibilidade técnica. |
| 5 | Proteger documentos gerados com armazenamento privado e links temporários. | PDFs deixam de depender de uma URL pública permanente e podem ser entregues novamente com segurança. |
| 6 | Mostrar erros de rede e dados desatualizados no painel. | Uma falha de carregamento não aparece como lista vazia ou saldo zerado. |
| 7 | Permitir navegar por todo o histórico de conversas, com paginação. | O escritório consulta atendimentos antigos sem ficar limitado às últimas mensagens. |
| 8 | Adaptar a experiência de conversas para celular. | Lista, conversa e ações de atendimento ficam utilizáveis em telas pequenas. |
| 9 | Transformar o onboarding em uma verificação real de prontidão. | O escritório sabe exatamente o que falta para receber o primeiro atendimento. |
| 10 | Consolidar o relatório financeiro com custos, créditos, compras e cobranças aos clientes finais. | A operação passa a enxergar margem e receita sem recalcular dados históricos. |

## Alterações implementadas

- **22/09/2026 — Processamento e envio:** a sugestão do ChatGPT após a reunião
  de 21/09/2026 foi implementada. A resposta gerada, o consumo e a pendência
  de entrega são gravados antes do envio ao WhatsApp. Falhas de entrega são
  recuperadas por uma fila própria, sem chamar a IA nem registrar nova cobrança.

As melhorias de interface devem manter a paleta, tipografia e identidade visual
já definidas pelo projeto. O foco é clareza, acesso em celular e feedback das
ações, não um redesign.

## 3 novas funcionalidades recomendadas

| Ordem | Funcionalidade | Escopo inicial | Benefício |
| --- | --- | --- | --- |
| 1 | Central de operação | Alertas para WhatsApp desconectado, mensagem não entregue, fila parada, arquivo com erro e saldo crítico. | Permite agir antes que uma falha afete o atendimento ao cliente. |
| 2 | Versões dos agentes | Rascunho, teste em conversa de exemplo, publicação e retorno à versão anterior. | Reduz o risco de alterar instruções e prejudicar atendimentos em andamento. |
| 3 | CRM jurídico enxuto | Contato, assunto, área jurídica, etapa, responsável, anotações e próxima ação. | Converte conversas em acompanhamento comercial e operacional do escritório. |

## Critério de execução

Priorizar primeiro a confiabilidade do atendimento e a proteção de dados; em
seguida, visibilidade operacional e experiência do escritório. As novas
funcionalidades devem entrar após a estabilização desses fluxos ou em frentes
independentes que não afetem o processamento de mensagens.
