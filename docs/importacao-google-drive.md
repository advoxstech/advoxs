# Importação manual do Google Drive

## Status da funcionalidade

**Em espera desde 26/09/2026.** O código está implementado e validado localmente,
mas a integração deve permanecer desativada com `GOOGLE_DRIVE_ENABLED=false`.
O upload manual de arquivos continua funcionando normalmente enquanto isso.

Para liberar a funcionalidade no futuro, ainda será necessário:

- criar ou selecionar o projeto do Advoxs no Google Cloud;
- habilitar as APIs Google Drive e Google Picker;
- configurar a tela de autorização, o escopo `drive.file` e os usuários de teste;
- criar o cliente OAuth para aplicação web e cadastrar as origens do painel;
- criar e restringir a API key para Drive, Picker e sites autorizados;
- preencher `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_API_KEY` e
  `GOOGLE_DRIVE_PROJECT_NUMBER` na `.env` da VPS;
- publicar o código e aplicar a migration **0042**;
- ativar `GOOGLE_DRIVE_ENABLED=true`, recriar os serviços e executar o teste real
  de autorização, seleção, importação e atualização de arquivos.

Não ativar a opção antes de concluir essa lista. As credenciais não devem ser
registradas neste documento nem versionadas no Git.

## Uso

Na base de conhecimento, clique em **Importar do Google Drive**, escolha o agente
e a categoria dos novos arquivos, autorize sua conta Google e selecione até 20
arquivos. Confira a lista e confirme a importação. Cada arquivo tem seu resultado;
o processamento pode continuar depois que o download terminar.
Importações interrompidas ainda em processamento são reenfileiradas automaticamente
após dez minutos. Falhas já identificadas podem ser retomadas pelo botão Reprocessar.

PDF, DOCX e TXT usam o processamento atual. Documentos Google são exportados como
DOCX. Planilhas, apresentações, pastas e atalhos não fazem parte desta versão.
O Google limita exportações de documentos nativos a 10 MB; downloads também
respeitam o limite configurado da base (20 MB por padrão).

Não existe sincronização automática nem acesso permanente à conta. Para atualizar,
selecione novamente o arquivo e marque explicitamente a atualização. Arquivos
inalterados são ignorados. Arquivos distintos com o mesmo nome exigem renomeação
no Drive. Atualizações mantêm o nome local, a categoria e todos os agentes vinculados.

## Atualização segura e histórico

Cada atualização é ingerida com um UUID separado, sem vínculos com os agentes.
Somente depois da ingestão bem-sucedida uma transação move os vínculos da versão
anterior para a nova. Se falhar, a versão anterior continua sendo usada. A tentativa
com erro aparece na lista e pode ser reprocessada ou excluída.

A versão anterior permanece privada para que fontes de respostas antigas abram o
documento correto. Ela não é vinculada a novas execuções dos agentes. Uma resposta
já em processamento pode continuar usando a versão anterior que recebeu no início.
Versões anteriores e tentativas ocupam a cota de armazenamento; o limite de quantidade
considera documentos atuais. Excluir o documento atual exclui também todo seu histórico
no Advoxs. Excluir apenas uma tentativa com erro preserva o documento atual.
Nenhuma exclusão ou edição é feita no Google Drive.

## Configuração do Google Cloud

1. Crie ou escolha um projeto da plataforma Advoxs.
2. Ative **Google Drive API** e **Google Picker API**.
3. Configure Google Auth Platform: nome, suporte, contatos, domínio e política de
   privacidade. Escolha público externo e cadastre usuários de teste inicialmente.
4. Cadastre o escopo `https://www.googleapis.com/auth/drive.file`, limitado aos
   arquivos compartilhados com a aplicação. Não solicite acesso ao Drive inteiro.
5. Crie cliente OAuth do tipo aplicação web. Cadastre as origens JavaScript exatas
   do painel (HTTPS em produção; localhost para desenvolvimento). O fluxo usa janela
   de autorização e não necessita de callback próprio nem segredo de cliente no Advoxs.
6. Crie a API key do seletor no mesmo projeto. Restrinja às APIs Drive/Picker e
   aos sites autorizados. Conforme documentação atual do Picker, inclua também
   `https://docs.google.com/*` para as requisições do iframe.
7. Copie o client ID, a API key e o **número** do projeto (não o nome nem o project ID).
8. Para liberação pública, confira publicação do público e verificação da marca.
   Não prometa disponibilidade geral enquanto a aplicação estiver em modo de teste.

Cada escritório só autoriza a própria conta; não cria projeto Google Cloud.

## Configuração e deploy

Variáveis novas e opcionais na `.env` da VPS:

```dotenv
GOOGLE_DRIVE_ENABLED=false
GOOGLE_DRIVE_CLIENT_ID=
GOOGLE_DRIVE_API_KEY=
GOOGLE_DRIVE_PROJECT_NUMBER=
```

Depois de preencher os valores, use `GOOGLE_DRIVE_ENABLED=true` e recrie o serviço
API pelo fluxo normal de deploy. A configuração do seletor é servida por uma rota
autenticada em runtime; não há valores Google embutidos no build do frontend.
Não alterar chaves do WhatsApp, agentes ou banco. Sem configuração completa, o
botão fica oculto, as rotas de importação recusam chamadas e o upload local continua.

A migration **0042** adiciona metadados opcionais à tabela existente e índices para
duplicados/atualizações. Usa a RLS já existente. Não há migration no serviço RAG.
O deploy precisa aplicar a migration antes de iniciar as novas versões de API/worker.
Rollback operacional: desative a integração mantendo o schema. O downgrade da migration
recusa remover dados se já houver histórico ou atualização pendente.

## Segurança e limites

- Token Google mantido só na memória do navegador e da requisição de download.
  Nunca gravado em banco, localStorage, logs, URLs Advoxs ou argumentos do worker.
- Sem refresh token; expiração exige nova seleção/autorização pelo usuário.
- A API usa o token apenas para leitura nas URLs fixas do Google; o Google valida
  acesso e permissão de download de cada arquivo. URLs do usuário não são acessadas.
- Nome, tipo, conteúdo e tamanho são validados no servidor. O download tem limite
  de bytes e de tempo; a versão é verificada novamente após o download.
- O tenant vem da sessão Advoxs, nunca do corpo enviado pelo navegador.
- Reservas de cota e publicação de atualizações são serializadas por escritório.
- Não ativar logs de corpo de requisição/headers na infraestrutura: eles podem conter
  autorização e documentos privados, tanto neste fluxo quanto no upload já existente.

## Validação antes da liberação

Executar testes de API, worker, frontend, lint e build. Com as credenciais Google,
testar em uma conta autorizada: seleção múltipla, conta diferente, PDF/DOCX/TXT,
Documento Google, cancelamento, token expirado, download proibido, documento grande,
atualização, duplicados e consultas dos agentes com fontes. A simulação local não
substitui essa etapa real. Credenciais não devem ser coladas em chats ou versionadas.

Validação local realizada em 25/09/2026: suites unitárias da API, worker e frontend;
Ruff (lint e formato), ESLint e build de produção do Next.js. Quatro testes com
PostgreSQL 16 isolado aplicaram a migration real e verificaram publicação, rollback,
isolamento por escritório com RLS e unicidade dos arquivos. A interface foi exercitada
em 1440 px e 390 px com respostas Google simuladas. O teste real de autorização,
seletor e download do Google continua pendente da configuração do Google Cloud.

Os testes opcionais de banco ficam em
`apps/worker/tests/integration/test_drive_publication_postgres.py` e exigem
`TEST_DRIVE_DATABASE_URL` apontando explicitamente para um banco descartável,
com permissão para criar schema/papel. O ambiente de testes precisa de Alembic
(disponível nas dependências da API); eles não usam a configuração de produção.

Referências oficiais:
- https://developers.google.com/workspace/drive/picker/guides/web-picker-sample
- https://developers.google.com/identity/oauth2/web/guides/use-token-model
- https://developers.google.com/workspace/drive/api/guides/manage-downloads
- https://developers.google.com/workspace/drive/api/guides/api-specific-auth
