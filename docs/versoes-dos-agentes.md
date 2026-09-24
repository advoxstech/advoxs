# Versões dos agentes

Implementado em 23/09/2026, a partir da sugestão do ChatGPT após a reunião de 21/09/2026 e do plano aprovado no chat.

## Funcionamento

Abra **Agentes → agente desejado**. A identidade visual existente foi preservada.

- **Configuração:** edite nome e instruções e escolha **Salvar rascunho**. Isso não muda o conteúdo usado pelos clientes. Alterações locais não salvas têm aviso e proteção ao sair por links ou fechar a página.
- **Publicar versão:** após salvar, informe uma descrição opcional e confirme a publicação. As próximas execuções usam a nova configuração. Uma execução já iniciada mantém sua cópia anterior; publicar não reinicia o histórico dos clientes.
- **Testar:** inicia uma conversa separada usando o rascunho salvo do agente selecionado e configurações publicadas dos demais agentes. Mantém o papel e as ferramentas de cada agente. Transferências durante o teste continuam funcionando. Nenhuma mensagem é enviada ao WhatsApp; o consumo é debitado dos créditos do escritório pelo fluxo de testes existente.
- **Versões:** histórico paginado com número, autor quando disponível, data, descrição e origem da restauração. A comparação mostra nome e instruções da versão escolhida ao lado da publicada.
- **Restaurar como rascunho:** pede confirmação antes de substituir o rascunho e alterações locais. A publicação seguinte recebe um novo número. Exemplo: recuperar a versão 1 após publicar a 2 gera a versão 3, sem apagar as anteriores.

Cada agente tem um rascunho compartilhado pelo escritório. Se outra sessão salvar, restaurar ou publicar, uma tentativa com revisão antiga retorna conflito e mantém o texto digitado na tela. Copie o texto necessário e recarregue para revisar o estado atual. Repetir uma publicação com a revisão anterior não gera versões duplicadas.

O teste guarda uma cópia das configurações usadas na sua criação. Salvar, restaurar ou publicar invalida essa revisão para novas mensagens: inicie outro teste. A execução de teste já iniciada pode terminar com sua cópia anterior. Os testes anteriores permanecem na área de conversas de teste até serem excluídos; uma conversa cujo agente foi excluído não passa silenciosamente a usar configurações publicadas.

## Escopo e limites

- Versionados: **nome e instruções**.
- Não versionados: documentos, conteúdo/vínculos da base de conhecimento, modelos de IA, regras de cobrança e definição do agente inicial. Restaurar instruções não recupera documentos removidos nem garante respostas idênticas às antigas.
- Novos agentes são criados com a versão 1 publicada. Agentes provisionados automaticamente também recebem versão 1. Registros anteriores à implantação são importados sem atribuir um autor histórico desconhecido.
- O histórico pertence ao agente: excluí-lo também exclui suas versões e os snapshots dos testes, mantendo as restrições de exclusão já existentes.
- A rota antiga de edição direta retorna 409 com orientação para atualizar a página. Clientes antigos não conseguem contornar a publicação explícita.
- Os testes reutilizam o cálculo e débito existentes; esta entrega não altera a política financeira das conversas de teste.

## Banco e deploy

A migração `0038_agent_versions` adiciona os campos de rascunho/revisão em `agents`, as tabelas `agent_versions` e `agent_test_sessions`, políticas de isolamento por escritório e um gatilho para registrar a versão inicial em qualquer caminho de criação. As instruções existentes são copiadas sem alteração para a versão 1.

Publicação e histórico são gravados na mesma transação, com bloqueio da linha do agente e verificação de revisão. Uma falha desfaz ambos. O worker continua lendo apenas `agents.name` e `agents.instructions`; rascunhos não entram no carregamento dos atendimentos reais.

Implantar API, serviço de agentes e frontend juntos pelo fluxo existente. A migração é aplicada por `alembic upgrade head`, já executado no deploy. **Não há novas variáveis no `.env` de produção, serviços ou configuração manual na VPS.** Antes de um downgrade que remova a migração, considerar que os rascunhos e o histórico de versões serão perdidos; a configuração publicada permanece em `agents`.

## Verificação

1. Edite um agente e salve um rascunho. Confira que o título/publicação ainda mantém o nome anterior.
2. Na aba Testar, inicie uma conversa e confira as instruções novas. Isso usa créditos reais quando conectado ao serviço de IA.
3. Publique após confirmar. Confira a nova versão e seu autor no histórico.
4. Recupere uma versão anterior e confira que ela só passa a valer depois de publicar novamente.
5. Abra o mesmo agente em duas abas. Salve em uma e tente salvar na outra: a segunda deve apresentar conflito sem apagar o texto digitado.
6. Altere o rascunho após iniciar um teste. Uma mensagem no teste antigo deve pedir um novo teste.

Os testes automatizados cobrem API, interface, isolamento do teste, preservação das ferramentas e contexto do agente, migração, RLS, publicação simultânea, restauração e rollback em falha de gravação. Chamadas reais de IA e WhatsApp não fazem parte dos testes automatizados.

Validação desta entrega: 641 testes unitários da API, 176 do serviço de agentes, 298 do frontend e 2 de integração em PostgreSQL 16 aprovados; lint, formatação e build do frontend aprovados. O fluxo de salvar, publicar, comparar, restaurar e testar também foi conferido no navegador em 1440 px e 390 px, com respostas simuladas e sem consumir créditos reais. Permanecem apenas os avisos preexistentes de imagens no lint do frontend.

O teste PostgreSQL usa **somente** a variável opcional de desenvolvimento/CI `AGENT_VERSIONS_TEST_DATABASE_URL`, apontando para banco descartável com permissão para criar schemas e roles. Ela não é necessária em produção. Executar em `apps/api`:

```bash
uv run pytest tests/integration/test_agent_versions.py
```

Sem essa variável, os testes de integração são pulados. O workflow do CI fornece um PostgreSQL temporário e executa esse arquivo explicitamente.
