# Múltiplos escritórios por conta

Implementação na branch `feat/multiplos-escritorios`, dependente da funcionalidade de equipe (0045). Migration: `0046`.

## Comportamento

- Um e-mail e senha identificam uma pessoa. Cada vínculo em `office_memberships` define escritório, perfil (`admin`/`lawyer`) e acesso ativo.
- Um único escritório ativo abre diretamente após o login. Vários exigem seleção. Sem vínculos ativos, a pessoa pode autenticar e aceitar convites, mas não acessa dados de escritórios.
- O cabeçalho informa o escritório atual e oferece **Trocar escritório**, tanto no desktop quanto no celular.
- A seleção é compartilhada entre abas da mesma sessão. Uma troca faz navegação completa, descartando dados e formulários do contexto anterior. Outras abas recebem um aviso de armazenamento e voltam à seleção. O servidor também rejeita tokens de contexto antigo e requisições cujo `X-Office-ID` não corresponda à sessão, mesmo sem comunicação entre abas.
- Sessões de navegadores diferentes mantêm seleções independentes. A renovação de tokens conserva o contexto da sessão no servidor. Trocas concorrentes usam bloqueio e versão; a seleção desatualizada recebe conflito.
- Perfis e desativações valem somente no escritório correspondente. O último administrador ativo continua protegido. Alterar a senha revoga sessões da identidade em todos os escritórios.
- Um vínculo desativado permanece no histórico. Ele requer reativação explícita pelo administrador; outro convite não o reativa automaticamente.

## Convites e alertas

- Conta nova: definir senha e aceitar. Conta existente: autenticar com o e-mail convidado e aceitar; senha, nome e outros vínculos não são sobrescritos.
- Convites têm validade de 48 horas e uso único. Apenas possuir o link não concede acesso a uma conta já existente. Aceitações concorrentes são serializadas, sem duplicar vínculos.
- O segredo chega no fragmento da URL, é removido da barra e mantido apenas na sessão da aba para sobreviver ao login. É removido após a aceitação.
- Administradores não recebem a lista de escritórios de outros usuários.
- Vínculos com agentes, tomadas de atendimento e permissões de documentos são limitados ao escritório ativo. Somente **Assumir atendimento** pausa a IA.
- Alertas verificam vínculo, perfil e situação atual antes de enviar. O link inclui o escritório e a conversa; a pessoa confirma o escritório correto antes de abrir o atendimento. Um link nunca concede acesso sozinho.
- O envio continua usando Gmail SMTP e a fila persistente existente. Não exige novo serviço nem Google Cloud OAuth. `WEB_APP_URL` deve apontar ao ambiente que contém o convite: convites do banco local não funcionam no banco de produção.

## Migração e publicação

1. Fazer backup do PostgreSQL e interromper temporariamente API/worker antigos durante a transição.
2. Publicar artefatos da API, painel e worker que incluam 0045 e 0046.
3. Executar `alembic upgrade head` com o usuário de migração, antes de retomar o tráfego e o worker.
4. Verificar SMTP, `WEB_APP_URL` e a chave de criptografia compartilhada, sem alterar a chave existente.
5. Pedir novo login: a migração incrementa a versão das sessões para invalidar tokens emitidos sob a regra anterior.
6. Validar convite novo/existente, seleção, perfis diferentes, revogação isolada, alertas e atendimento antes de liberar amplamente.

A migração cria os vínculos a partir de `users`, preservando IDs, senhas e histórico. A antiga desativação de usuário é copiada para o vínculo; `users.active` passa a representar bloqueio global da identidade. Um índice único em `lower(email)` impede contas duplicadas por capitalização. Duplicatas legadas fazem a migração abortar sem mesclar contas automaticamente.

`users.tenant_id` e `users.role` são mantidos temporariamente como dados legados de criação, sem uso para autorização. Um trigger cria o primeiro vínculo junto com novos usuários, preservando o cadastro financeiro existente. Sua remoção fica para uma migração de limpeza após a transição. A RLS de `users` passa a usar os vínculos; os vínculos e as referências de responsáveis/agentes também têm isolamento no banco.

Não há downgrade automático: o esquema antigo não representa vários vínculos sem perder acessos. Rollback exige restauração coordenada do backup e das versões de todos os serviços. Não iniciar a aplicação antiga sobre o esquema novo.

## Validação

- PostgreSQL descartável: migração de usuário desativado, senha preservada, vínculos separados, papéis por escritório, RLS, referências inválidas, aceitação concorrente e trocas concorrentes.
- Autenticação: seleção obrigatória, contexto antigo rejeitado, refresh após troca, acesso suspenso e revogação independente.
- Interface: contas existentes sem campo de nova senha, retorno do login com convite, listagem de escritórios e envio do contexto nas ações. Verificação de origem aceita a porta pública do Docker e rejeita origens externas.
- Worker: envio concorrente, destinatários desativados, vínculo em outro escritório e links com identificação do escritório.

Testes usam bancos descartáveis explicitamente configurados. Não enviam convites nem alertas para destinatários reais.
