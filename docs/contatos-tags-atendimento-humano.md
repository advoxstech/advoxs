# Contatos, tags e atendimento exclusivamente humano

## Uso

Em **Conversas → Contatos e tags**, o administrador cadastra nome, WhatsApp completo
(país + DDD + número), tags e a opção **Atendimento exclusivamente humano**.
Também pode abrir o cadastro a partir de uma conversa existente.

As tags são próprias de cada escritório e não são enviadas ao contato. A lista
de conversas permite escolher uma tag, todas ou sem tag. Advogados continuam
vendo somente as conversas permitidas pelas regras de equipe; o cadastro é
gerenciado pelo administrador.

Quando o número cadastrado envia uma mensagem, tanto pela Meta quanto pela Z-API,
a conversa recebe o vínculo e as tags. Com a regra humana ativa, a IA não responde,
o fluxo automático de cobrança não inicia e respostas pendentes são canceladas.
A equipe clica em **Assumir atendimento** para responder pelo painel.

Desativar a regra ou remover o cadastro não devolve uma conversa existente à IA.
É necessário usar **Devolver para IA** depois. Remover uma tag não altera a regra.
Apagar uma conversa não apaga o cadastro do contato.

## Persistência e concorrência

- Migration `0048`: contatos, tags, vínculos, RLS e roteamento das conversas.
- O número é comparado sem espaços, pontuação ou prefixo `00`. O sistema não
  acrescenta país nem altera o nono dígito. Use o número que chega pelo WhatsApp.
- O vínculo é restrito ao escritório. Conversas de teste não recebem a regra.
- A criação simultânea do contato e da conversa é serializada pelo número.
- Alterações no cadastro atualizam conversas existentes na mesma transação.
- A geração e a entrega revalidam o estado da conversa. Uma chamada externa já
  iniciada não pode ser desfeita; a ativação bloqueia as entregas posteriores.
- O bloqueio inclui avisos automáticos de compra, assinatura e mudança de isenção.
  Os efeitos financeiros das operações continuam sendo registrados normalmente.

## Anexos

As referências dos anexos permanecem nas mensagens. O painel permite baixá-los
sob demanda, com autorização por conversa e limite de 20 MB, sem chamar IA/RAG.
O download depende de o provedor ainda disponibilizar o arquivo: esta entrega
não cria um arquivo permanente de mídias. Links expirados exibem erro no painel.
Downloads aceitam apenas HTTPS em domínios de mídia permitidos; não seguem
redirecionamentos nem repassam credenciais da Z-API ao armazenamento do Google.

## Publicação e validação

Aplicar `alembic upgrade head` no serviço da API antes de iniciar a nova versão
da API e do worker, e publicar o frontend correspondente. Nenhuma credencial
externa adicional é necessária. A migração deve ser executada pelo processo
normal de deploy, com o usuário de banco responsável pelas migrações.

Testes cobrem cadastro e permissões, isolamento, filtragem antes da paginação,
entrada Meta/Z-API, cancelamento de respostas pendentes, concorrência, exclusão
e downloads. A CI inclui `tests/integration/test_contacts_postgres.py`, com
`CONTACTS_TEST_DATABASE_URL` apontando exclusivamente a um PostgreSQL descartável.

Para homologar: cadastre um número de teste com uma tag e atendimento humano;
envie texto e documento pelo WhatsApp, confira o filtro, assuma e responda.
Teste também um número fora da lista, que deve seguir o atendimento normal.
Nenhuma mensagem real foi enviada durante os testes automatizados.
