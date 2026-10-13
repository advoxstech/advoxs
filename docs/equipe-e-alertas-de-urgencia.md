# Equipe do escritório e alertas de urgência

Implementação: branch `codex/equipe-alertas-urgencia`, migration `0045` (após `0044`). O acesso a vários escritórios está na migration `0046`.

## Regras

- Há somente dois perfis: administrador e advogado. Todos os advogados têm a mesma hierarquia.
- O administrador convida membros, gerencia acessos e vincula vários advogados a cada agente.
- O advogado entra no mesmo login, com seu próprio e-mail e senha. O convite cria um usuário no escritório existente, sem novo escritório ou assinatura.
- Uma conta pode participar de vários escritórios por vínculos independentes. Convites para e-mails existentes exigem login com a conta convidada; não alteram sua senha nem outros acessos. Ver [múltiplos escritórios](multiplos-escritorios.md).
- Advogados acessam conversas reais dos agentes vinculados e conversas atribuídas diretamente a eles. Não acessam testes, configurações, finanças, gestão da base ou equipe. Documentos citados ficam disponíveis apenas em conversas autorizadas.
- Somente o clique em **Assumir atendimento** pausa a IA. Urgência, abertura do painel e envio do e-mail não pausam.
- A primeira solicitação que obtém o bloqueio da conversa assume. A segunda recebe conflito e deve atualizar o painel. Somente o responsável envia respostas manuais; o administrador pode transferir o atendimento.
- O responsável ou administrador pode devolver o atendimento à IA. Resolver a urgência é uma ação independente.
- A desativação revoga o acesso apenas ao escritório correspondente e retira a atribuição humana e mantém a IA pausada. O administrador precisa assumir, transferir ou devolver à IA. O último administrador ativo não pode ser desativado ou rebaixado.

## Como usar

1. Em **Equipe**, envie o convite. O destinatário abre o link, define sua senha e entra pelo login normal.
2. Em **Agentes → agente → Urgências**, selecione os advogados responsáveis e salve.
3. Uma conversa urgente gera e-mail para todos os responsáveis ativos. Sem advogado ativo vinculado, os administradores recebem o alerta.
4. O link abre a conversa após autenticação. O advogado pode assumir e responder, consultar o histórico ou devolver à IA.
5. **Meus atendimentos** filtra conversas assumidas pelo usuário. **Meus agentes** lista os vínculos do advogado. **Minha conta** permite alterar a própria senha.
6. O administrador acompanha a fila em **Equipe → Envios recentes**, podendo tentar novamente envios que falharam.

## Convites e e-mails

- Convite válido por 48 horas, uso único; cancelamento ou reenvio invalida o anterior. Reenvio exige intervalo de um minuto; limite de 30 convites por escritório por hora.
- O banco guarda o hash do convite e uma cópia cifrada temporária para envio. O segredo cifrado é removido após envio ou cancelamento. O link usa fragmento e a página o remove da barra de endereço; abrir o link sozinho não aceita o convite.
- Alertas são persistidos na mesma transação que marca a urgência. Um episódio gera no máximo um registro por destinatário. Resolver e sinalizar novamente cria outro episódio.
- Mudanças de agente, vínculos e situação dos membros reavaliam os destinatários. Antes de enviar, o worker confirma acesso atual, urgência ainda aberta e escritório ativo.
- Conversas de teste não geram alertas por e-mail. O e-mail contém escritório, agente, horário e link; o conteúdo do cliente permanece no painel autenticado.
- A fila usa bloqueios, concessão de cinco minutos e até cinco tentativas com espera progressiva. Falha de autenticação SMTP ou destinatário rejeitado exige intervenção. O administrador pode solicitar nova tentativa.
- O estado **Enviado** significa aceitação pelo servidor SMTP, não confirmação de leitura ou chegada à caixa de entrada. Como em outras filas com SMTP, uma interrupção após envio e antes de salvar o resultado pode causar reenvio; o Message-ID é estável, mas isso não garante deduplicação pelo destinatário.
- Eventos de assumir, transferir, devolver à IA, resolver/sinalizar urgência e desativar responsável ficam no histórico. Mensagens manuais identificam seu autor. A exclusão segura também remove histórico de atendimento e notificações vinculadas.

## Publicação e configuração

1. Aplicar a migration `0045` antes de iniciar as novas versões da API e do worker. Publicar API, worker e web juntos.
2. Confirmar `WEB_APP_URL` com a URL pública HTTPS do painel no ambiente compartilhado pelo worker.
3. Confirmar `GMAIL_SMTP_USER` e `GMAIL_SMTP_APP_PASSWORD` no worker. O envio reutiliza o SMTP Gmail já previsto no projeto; não exige Google Cloud OAuth.
4. API e worker precisam compartilhar o `WHATSAPP_TOKEN_ENCRYPTION_KEY` existente. Não trocar a chave de produção: ela também protege credenciais já armazenadas.
5. Com dois e-mails de teste autorizados pelo escritório, validar convite, login, vínculos, alerta e tomada do atendimento. Esta implementação foi testada localmente com envio SMTP simulado; a entrega real depende dessas configurações e deve ser conferida após o deploy.

O downgrade remove os dados novos de equipe/alertas e atribuição. Para rollback, priorizar restaurar a versão da aplicação de forma coordenada e preservar os dados; não executar downgrade sem planejamento.

## Validação automatizada

- API: permissões por perfil, conta desativada, perfil atual sobrepondo o JWT antigo e testes existentes.
- PostgreSQL: upgrade/downgrade, isolamento da equipe por escritório, deduplicação, fallback, exclusão de testes, convite de uso único, concorrência ao assumir e proteção do último administrador.
- Worker: seleção atual dos destinatários, cancelamento de alertas obsoletos, tentativas, retomada de concessão vencida e dois workers competindo pela mesma notificação, sem envio externo nos testes.
- Interface: menus por perfil, múltiplos responsáveis, clique explícito para pausar e aceitação de convite compatível com StrictMode.

Testes PostgreSQL usam apenas bancos descartáveis explicitamente informados por `AGENT_VERSIONS_TEST_DATABASE_URL` (API) e `CONVERSATION_CLEANUP_TEST_DATABASE_URL` (worker).
