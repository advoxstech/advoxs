# Política de logs seguros

Esta política reduz a exposição de dados pessoais e credenciais sem retirar as informações
necessárias para investigar falhas em produção.

## O que pode ser registrado

- IDs internos em formato UUID, como tenant, conversa, mensagem, documento e trabalho da fila.
- Contagens, tamanhos, duração, etapa da operação e quantidade de tentativas.
- Nome da operação, provedor e status HTTP.
- Tipo da exceção, sem copiar sua mensagem completa.
- URLs reduzidas a esquema, host e porta.

## O que não deve ser registrado

- Texto de mensagens, prompts, respostas da IA, consultas ou trechos de documentos.
- Telefone, identificadores externos do provedor e nomes de arquivos enviados pelo usuário.
- Tokens, senhas, chaves, segredos, headers de autenticação ou a URL do banco.
- Corpo de respostas de serviços externos.
- Caminhos e URLs completos, pois podem conter credenciais, nomes ou links temporários.
- A mensagem completa de exceções externas, que pode repetir URL, payload ou credencial.

## Identificadores e erros

Cada serviço possui um módulo `safe_logging` porque API, worker, agents e api_rag são imagens
independentes. Os helpers seguem o mesmo contrato:

- `safe_identifier`: preserva UUIDs internos e transforma outros valores em uma referência curta
  pseudonimizada. O mesmo valor produz a mesma referência, o que permite correlacionar eventos.
- `safe_error`: registra somente a classe da exceção.
- `safe_url`: preserva esquema, host e porta, removendo usuário, senha, caminho e query string.

Uma falha de provedor deve registrar, por exemplo, operação, status HTTP, referência da conversa,
tentativa e `error_type`. O conteúdo enviado e a resposta do provedor ficam fora do log.

## Níveis e retenção

- `DEBUG`: métricas detalhadas de execução, sem conteúdo do usuário.
- `INFO`: início, conclusão e transições esperadas.
- `WARNING`: falhas recuperáveis ou entradas recusadas.
- `ERROR`: falhas definitivas ou que exigem intervenção.

O Docker Compose mantém rotação dos logs com `max-size: 10m` e `max-file: 5`. Alterações nesses
limites devem considerar capacidade do servidor e necessidade operacional, sem aumentar a
quantidade de dados sensíveis registrada.

## Verificação automática

O comando `python scripts/check_sensitive_logs.py` analisa o código de produção e falha quando
encontra valores sensíveis enviados diretamente ao logger ou a URL do PostgreSQL em `print`. A
verificação também roda no CI. Cada serviço testa os helpers para impedir regressões na remoção de
credenciais, caminhos, query strings e identificadores externos.
