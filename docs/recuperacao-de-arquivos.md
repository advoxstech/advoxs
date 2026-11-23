# Recuperação de arquivos e consistência da busca

As migrações 0055 e 0056 devem ser aplicadas antes de iniciar a API e os workers atualizados.
O deploy existente executa `docker compose run --rm api uv run alembic upgrade head`.
São necessários o worker normal (recuperação periódica) e o worker-ingestion (execução).

## Arquivos

O trigger de `knowledge_base_files` registra a intenção em `knowledge_file_tasks` na mesma
transação do arquivo. Não há janela entre o commit e a fila que possa perder a tarefa.
A migração inclui arquivos legados em processamento, inclusive uploads manuais e pacotes.

O worker normal procura tarefas pendentes a cada minuto. A ingestão usa uma concessão de
35 minutos, maior que seu timeout de 30 minutos, e um token para impedir finalização por
uma execução antiga. Interrupções são recuperadas após a concessão expirar. Falhas têm
espera progressiva e no máximo cinco tentativas; depois o painel permite reprocessar.

A exclusão registra uma intenção e retira o arquivo das novas consultas imediatamente.
A busca é limpa antes da remoção definitiva do registro. Falhas mantêm a tarefa para
nova tentativa. Se o limite for atingido, o arquivo reaparece com erro de limpeza e o
administrador pode clicar em Excluir novamente. A exclusão continua respondendo HTTP 204,
agora confirmando a aceitação da intenção, não a conclusão da limpeza externa.

Atualizações do Drive e de pacotes usam um novo UUID. A versão anterior continua sendo
consultada até a publicação atômica da nova. Os originais históricos ficam preservados
para as fontes de respostas antigas; não entram em novas buscas. Excluir o arquivo do
Drive remove também suas versões históricas. Documentos compartilhados entre agentes
só são removidos na troca de especialidade quando não restar nenhum vínculo.

## Z-API tradicional

Uma consulta que confirma desconexão atualiza o painel e registra que houve uma queda
detectada. Uma mensagem autenticada posterior recupera esse estado. A desconexão manual
limpa essa marca e não é revertida por uma mensagem tardia. Erros de consulta mantêm o
último estado conhecido e mostram “status não confirmado”. Omni mantém seus callbacks.

## Verificação

Os testes de PostgreSQL em `apps/worker/tests/integration/test_file_tasks_postgres.py`
usam `FILE_TASKS_TEST_DATABASE_URL`, exclusivamente em banco descartável. Cobrem perda
do disparo Redis, concorrência, concessão expirada, rollback, limpeza idempotente,
limite de tentativas e isolamento entre escritórios. Esse banco é configurado no CI.
