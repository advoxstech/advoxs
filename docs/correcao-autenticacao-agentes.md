# Correção do erro 503 dos agentes — 24/09/2026

## Causa identificada

O serviço estava com `APP_ENV=production` e `AGENTS_API_KEY` vazia. A proteção
das rotas recusava mensagens e atualizações de contexto com HTTP 503 antes de
executar a IA. Suspender a validação inicial de produção não havia suspendido
essa proteção das requisições. O aviso de Langfuse sem chave é independente.

## Correção no deploy

- Após baixar as imagens e antes de parar os serviços, o deploy executa
  `scripts/ensure_agents_api_key.py` usando o Python e o python-dotenv da imagem
  da API. Não exige instalar programas ou preencher segredos manualmente na VPS.
- Se a chave estiver ausente, vazia ou só com espaços, gera 32 bytes aleatórios
  em hexadecimal e grava `AGENTS_API_KEY` no `.env` existente de `/opt/advoxs`.
- Uma chave já preenchida é preservada. As outras configurações também são
  preservadas. A escrita é atômica e mantém o proprietário do arquivo, com
  permissões de leitura e escrita somente para ele. O valor não é impresso.
- `api`, `worker` e `agents` já compartilham esse `.env` no Compose. O deploy
  verifica que cada serviço recebe uma chave preenchida antes de prosseguir.
- Deploys desse workflow são serializados para evitar alterações simultâneas.
- A autenticação interna continua exigida em produção. Esta correção não
  reativa a validação geral de todos os segredos nem altera chaves JWT, RAG,
  credenciais da Meta ou da Z-API.

## Aplicação e confirmação

A correção está preparada no repositório; só terá efeito na VPS após publicar
essas alterações e concluir o deploy. A recriação dos serviços usa o fluxo já
existente, inclusive sua interrupção durante a implantação.

Depois do deploy, testar uma conversa e conferir os logs do worker e dos
agentes. As chamadas não devem mais falhar com 503 por ausência de autenticação
interna. Outras falhas, se existirem, precisam ser investigadas separadamente.
Não alterar `APP_ENV` para desenvolvimento nem desativar a autenticação como
solução para este incidente. A chave persiste entre deploys; não há rotação
automática e não se deve adicionar o `.env` ao Git.

## Validação local

Testes com arquivos temporários cobrem geração, preservação de configurações,
reexecução sem rotação, entradas duplicadas, ausência do arquivo e falha na
substituição. Os testes não acessam a VPS nem usam credenciais reais.
