# Limites de arquivos e exclusão segura de conversas

## Limites durante a leitura

Uploads de documentos, logos e anexos de teste são lidos em partes. A leitura é interrompida no primeiro trecho que excede o limite configurado, evitando manter arquivos grandes inteiros na memória da API.

O worker aplica a mesma proteção ao baixar mídias recebidas pelo WhatsApp. Quando o provedor informa o tamanho, o download é recusado antes de começar. Quando não informa, o conteúdo é acompanhado durante a transferência e interrompido assim que passa do limite.

Os limites existentes continuam sendo usados; esta entrega não adiciona variáveis de ambiente.

## Exclusão segura de conversas

Ao confirmar a exclusão, a API registra uma pendência durável no banco e a conversa deixa de aparecer nas telas e indicadores. O worker conclui em segundo plano a remoção de:

- mensagens e trabalhos associados;
- anexos pessoais armazenados na base de conhecimento;
- memória da conversa mantida pelo serviço de agentes.

Se um serviço estiver indisponível, a pendência permanece no banco e recebe novas tentativas automáticas. O histórico financeiro e de uso é preservado para auditoria, sem manter vínculo com as mensagens removidas.

Se uma nova mensagem chegar enquanto a limpeza estiver em andamento, ela não é apagada. A conversa é reiniciada sem resumo ou contexto antigo e essa mensagem segue para processamento.

## Implantação e validação

A implantação executa a migração `0040_durable_conversation_cleanup.py` e requer a atualização conjunta da API e do worker. Não há nova configuração de ambiente ou serviço externo.

Os testes cobrem leitura interrompida no limite, download de mídias, criação e recuperação da pendência de exclusão, indisponibilidade dos serviços externos, preservação da auditoria e chegada concorrente de uma nova mensagem. Os cenários de persistência também são executados em PostgreSQL descartável no CI.
