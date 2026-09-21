# Configuração de produção

Os quatro serviços Python validam a configuração de segurança antes de
atender requisições ou consumir a fila. A validação é local: não consulta
provedores externos e não confirma se uma credencial foi revogada.

## Ambiente

- Execução direta: `APP_ENV=development|test|production`; se omitido, usa
  `development`. Valores desconhecidos são rejeitados.
- O `docker-compose.yml` fixa `production` nos quatro serviços Python,
  independentemente de `APP_ENV` no arquivo `.env`.
- O `docker-compose.override.yml` substitui o ambiente por `development`
  apenas no desenvolvimento local.
- Em produção, use explicitamente `docker compose -f docker-compose.yml`.
  O workflow de deploy já seleciona esse arquivo.

## Exigência de segredos

A validação está temporariamente desligada por padrão para permitir a
continuidade dos deploys enquanto o servidor não pode ter o `.env` atualizado.
Quando os valores abaixo estiverem preenchidos, adicione
`ENFORCE_PRODUCTION_CONFIG=true` ao `.env` para voltar a bloquear deploys com
segredos ausentes ou inválidos.

Com essa opção habilitada, as seguintes variáveis são exigidas em produção:

| Serviço | Variáveis exigidas em produção |
| --- | --- |
| API | `JWT_SECRET`, `PLATFORM_JWT_SECRET`, `AGENTS_API_KEY`, `INTERNAL_SERVICE_KEY`, `RAG_API_KEY`, `WHATSAPP_TOKEN_ENCRYPTION_KEY`, `TENANT_STRIPE_KEY_ENCRYPTION_KEY` |
| Worker | `AGENTS_API_KEY`, `INTERNAL_SERVICE_KEY`, `RAG_API_KEY`, `WHATSAPP_TOKEN_ENCRYPTION_KEY` |
| Agents | `AGENTS_API_KEY`, `RAG_API_KEY` |
| RAG | `API_KEY` — o Compose a obtém de `RAG_API_KEY` |

Valores vazios, somente espaços, espaços nas extremidades e placeholders como
`changeme` são rejeitados. Os segredos JWT devem ter pelo menos 32 caracteres
e ser diferentes entre si. Chaves de criptografia devem ter formato Fernet
válido. Erros exibem somente os nomes das variáveis inválidas.

Use os mesmos valores entre quem envia e quem recebe: API/worker/agents para
`AGENTS_API_KEY`; API/worker para `INTERNAL_SERVICE_KEY`; API/worker/agents/RAG
para a chave do RAG. O Compose da raiz usa um único `.env`.

## Integrações

As duas opções de Stripe abaixo são `true` por padrão e aceitam `true/false`
ou `1/0`. Em produção, integrações habilitadas exigem:

| Opção | Segredos adicionais na API |
| --- | --- |
| `STRIPE_ENABLED` | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` |
| `STRIPE_CONNECT_ENABLED` | `STRIPE_CONNECT_SECRET_KEY`, `STRIPE_CONNECT_WEBHOOK_SECRET` |

Meta não é pré-requisito global do deploy: o provedor de WhatsApp é escolhido
por escritório, e tenants Z-API podem operar sem uma configuração Meta na
plataforma. Ao conectar pela Meta, o escritório informa o App Secret do
próprio aplicativo; ele é cifrado e recebe uma URL exclusiva de webhook.
Sua ausência não impede a plataforma de iniciar.
Desabilitar Meta bloqueia seu webhook e as rotas de
conexão/configuração do webhook, preservando a configuração independente da
Z-API. Desabilitar Stripe bloqueia o cadastro, a compra de créditos da plataforma e seu webhook.
Desabilitar Connect bloqueia onboarding, consulta de repasses, webhook e a
criação de checkout de clientes com esse provedor. As rotas bloqueadas retornam
503; o checkout interno sem Connect disponível segue o tratamento existente
de cobrança não configurada.

Essas opções controlam a admissão de chamadas; não cancelam jobs já enfileirados,
pagamentos ou assinaturas existentes. Não desabilite um provedor em uso sem
planejar o tratamento dos eventos pendentes. O Stripe standalone e a Z-API
continuam verificando suas credenciais por escritório.

## Preparação e deploy

1. Preencha os segredos no ambiente do servidor e configure explicitamente
   quais integrações serão usadas. Depois, defina
   `ENFORCE_PRODUCTION_CONFIG=true` para ativar a proteção no deploy.
2. Preserve as chaves Fernet existentes. Trocar essas chaves sem migrar os
   dados cifrados impede a leitura das credenciais armazenadas.
3. Com as novas imagens disponíveis, execute a validação:

   ```bash
   for service in api worker agents api_rag; do
     docker compose -f docker-compose.yml run --rm --no-deps --entrypoint /app/.venv/bin/python "$service" -m config_validation "$service" || exit 1
   done
   ```

4. O workflow executa essa mesma verificação depois do pull das imagens e
   antes de `docker compose down`. Enquanto
   `ENFORCE_PRODUCTION_CONFIG` estiver desligada, a verificação não bloqueia o
   deploy. Quando habilitada, uma configuração inválida interrompe o deploy sem
   parar os containers atuais. As migrações e a subida seguem somente após a
   aprovação da verificação.

O comando acima valida a política de segurança. Disponibilidade de banco,
Redis, Qdrant e credenciais externas continua sendo verificada durante a
operação; ele não é um teste de saúde completo.

## Manutenção e testes

Cada serviço tem uma cópia de `config_validation.py`, pois as imagens são
construídas com contextos independentes. Altere as quatro juntas. A suíte
`python -m unittest discover -s tests -p test_production_config.py -v`
verifica a política e a igualdade entre as cópias, inclusive no CI.
Os testes da API cobrem bloqueio de rotas desabilitadas e autenticação.
