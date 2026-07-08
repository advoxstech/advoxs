# Design — Conexão manual de WhatsApp Business

Data: 2026-07-08
Status: aprovado

## Objetivo

Permitir que cada escritório conecte seu próprio número de WhatsApp Business pelo painel, sem intervenção manual no banco (hoje o número/token entra via `scripts/seed_dev.py` ou inserção direta). O escritório faz o setup do lado da Meta (app, System User, token permanente com as permissões `whatsapp_business_management`/`whatsapp_business_messaging`, verificação do número) e cola 4 dados num formulário: **Phone Number ID**, **WABA ID**, **Access Token**, **PIN de 6 dígitos**. O `api` valida tudo contra a Graph API antes de persistir qualquer coisa.

## Decisão de arquitetura: substitui o Embedded Signup no roadmap

O Embedded Signup (OAuth da Meta, exige a plataforma ser aprovada como Tech Provider/Solution Partner) sai do plano — processo de aprovação incerto e lento. Este modelo manual (mesmo usado por Chatwoot/Chatvolt) não depende de aprovação da Meta: o próprio escritório assume o setup do lado dele.

Reaproveita quase integralmente o que já existe:
- Tabela `whatsapp_numbers` (`tenant_id` UNIQUE, `phone_number_id` UNIQUE, `waba_id`, `display_phone_number`, `access_token_encrypted`, `status`, `connected_at`) — sem mudança de schema.
- Webhook de entrada (`POST /api/v1/webhooks/whatsapp`) — resolve tenant por `phone_number_id`, sem mudança.
- Envio via Graph API — `worker` (resposta do agente) e `api` (take-over humano) já filtram por `status == "connected"` antes de enviar (`app/tasks/messages.py:131`, `app/api/v1/conversations.py:94`). **Desconectar já tem efeito real sem tocar nesses dois pontos.**
- Cifra do token (Fernet, `app/core/crypto.py`) — `encrypt_access_token`/`decrypt_access_token` já existem.

O que falta, e é o escopo desta entrega: o formulário de conexão e o endpoint que valida + registra + persiste.

## Decisões de produto

- **Modelo 1:1 mantido**: um número por escritório (constraint já existente, sem mudança).
- **Reconexão**: enviar o formulário de novo com um tenant já conectado **substitui** a linha existente (upsert por `tenant_id`), sem exigir desconectar antes.
- **`display_phone_number`**: obtido automaticamente via `GET` na Graph API (não é pedido no formulário) — valida o token/phone_number_id de graça antes mesmo do `/register`.
- **PIN**: usar e descartar — nunca persistido no banco, só passa pela request.
- **Desconexão entra nesta entrega**: endpoint que marca `status=disconnected` (sem apagar a linha).
- **Localização no painel**: `/configuracoes/whatsapp` (nova seção "configurações", abre espaço para outras configurações do escritório no futuro).

## Endpoints (`api`)

Novo router `app/api/v1/whatsapp.py` (prefix `/whatsapp`), autenticado com `get_current_tenant` + `get_tenant_session` (mesmo padrão das demais rotas tenant-scoped).

### `POST /api/v1/whatsapp/connect`

Body: `{ phone_number_id, waba_id, access_token, pin }`. Fluxo:

1. Valida o payload (campos obrigatórios; PIN com exatamente 6 dígitos numéricos) — `422` do Pydantic em caso de formato inválido.
2. `GET {graph_api_base_url}/{graph_api_version}/{phone_number_id}?fields=display_phone_number` com `Authorization: Bearer {access_token}`. Erro (token inválido, sem permissão, phone_number_id inexistente sob esse token) → `400` com mensagem clara em pt-BR; **nada é persistido**.
3. `POST {graph_api_base_url}/{graph_api_version}/{phone_number_id}/register` com `{"messaging_product": "whatsapp", "pin": "<pin>"}` e o mesmo header de auth. Erro (PIN incorreto, número já registrado) → `400` com a mensagem retornada pela Meta; **nada é persistido**. O `pin` do payload nunca é logado nem gravado.
4. Cifra o `access_token` (`encrypt_access_token`) e faz upsert em `whatsapp_numbers`: busca por `tenant_id`; se existir, atualiza os campos (`phone_number_id`, `waba_id`, `display_phone_number`, `access_token_encrypted`, `status="connected"`, `connected_at=now()`); se não existir, insere uma linha nova.
5. Se o `phone_number_id` já pertencer a **outro** tenant, o commit do passo 4 dispara a constraint `unique` existente na coluna — capturado (`IntegrityError`) e traduzido para `409` com mensagem explícita ("Este número já está conectado a outro escritório"), não um erro genérico de banco.
6. Retorna `200` com o estado da conexão (ver schema de `GET /connection` abaixo) — todo o trabalho é síncrono e rápido (chamadas HTTP diretas à Meta), sem fila/job assíncrono.

Erro de rede ao chamar a Meta em qualquer uma das duas chamadas → `502`, mensagem genérica em pt-BR (log do erro real, mesmo padrão do client do `api_rag` na base de conhecimento).

### `GET /api/v1/whatsapp/connection`

Retorna o estado atual do tenant ou `null` se nunca conectado:

```json
{ "display_phone_number": "+55 11 ****-1234", "status": "connected", "connected_at": "2026-07-08T12:00:00Z" }
```

O `display_phone_number` é mascarado na resposta (mantém DDD e os 4 últimos dígitos — mesma lógica de mascaramento, implementada uma vez e usada tanto aqui quanto no retorno do `connect`). **O `access_token` nunca aparece em nenhuma resposta desta API.**

### `POST /api/v1/whatsapp/disconnect`

Sem body. Marca `status="disconnected"` na linha do tenant (`404` se o tenant nunca conectou nenhum número). Não apaga a linha (mantém o histórico de `connected_at` original) — reconectar depois exige preencher o formulário de novo de qualquer forma, já que o token pode ter mudado do lado da Meta.

## Página (`web`)

Nova página `/configuracoes/whatsapp`: server component + `WhatsAppConnectionPanel` (client component), seguindo o padrão visual/estrutural de `/conversas` e `/base-de-conhecimento` (nav lateral com link novo apontando pra essa rota; middleware protegendo `/configuracoes/:path*`).

Ao montar, chama `GET connection`:

- **`null` (nunca conectado)**: mostra o formulário vazio — Phone Number ID, WABA ID, Access Token (campo tipo password), PIN (campo tipo password, `maxlength=6`, `inputmode=numeric`). Botão "Conectar".
- **`status=connected`**: mostra o número mascarado, badge de status (verde, mesmo token de cor usado em "pronto" na base de conhecimento) e a data de conexão. Botão "Desconectar" e link "Trocar número" (reabre o formulário; reenvio = substituição).
- **`status=disconnected`**: mesma tela de conectado, mas com o badge em latão (mesmo token usado em "processando"/atenção) e o botão principal é "Reconectar" (reabre o formulário).

Erros da API (`400`/`409`/`502`) aparecem como mensagem inline acima do formulário, mesmo padrão de feedback do `KnowledgeBasePanel` (`setFeedback`).

## Segurança

- Token cifrado em repouso (Fernet, já implementado); nunca retorna em texto puro em nenhuma resposta da API.
- PIN nunca persistido — só passa pela request, nunca logado.
- Todas as rotas autenticadas e tenant-scoped (RLS ativo via `get_tenant_session`).

## Erros e casos-limite

| Caso | Comportamento |
|---|---|
| Token inválido ou sem permissão | `400` no `GET` de validação — nada persiste |
| PIN incorreto | `400` no `/register` — nada persiste |
| `phone_number_id` já conectado a outro tenant | `409` explícito (constraint unique) |
| Reconexão (tenant já tem número) | Substitui a linha existente (upsert por `tenant_id`) |
| Desconectar sem nunca ter conectado | `404` |
| Desconectar | `status=disconnected`; `worker` e take-over humano já param de enviar (filtro existente, sem mudança) |
| Falha de rede ao chamar a Meta | `502`, mensagem genérica em pt-BR, log do erro real |
| Payload malformado (PIN com formato errado, campo faltando) | `422` (validação Pydantic) |

## Testes

- **api** (`tests/unit/test_whatsapp_connection_routes.py`): conexão feliz (mocks do `GET`+`register` da Meta), falha no `GET` (400, nada salvo — assert que não houve `commit`/insert), falha no `register` (400/409 conforme erro simulado, nada salvo), reconexão substitui a linha existente, `phone_number_id` de outro tenant → 409, desconectar com sucesso, desconectar sem conexão prévia → 404, `GET /connection` sem número conectado → `null`, mascaramento do `display_phone_number` na resposta.
- **web** (Vitest): formulário vazio quando `GET connection` retorna `null`, tela de "conectado" com número mascarado e botão desconectar, tela de "desconectado" com botão reconectar, erro inline em resposta de erro da API.

## Fora de escopo desta entrega

- Múltiplos números por tenant (schema já é 1:1, decisão confirmada, sem mudança).
- Re-registro automático usando um PIN salvo (decidido: usar e descartar — se precisar re-registrar no futuro, o tenant digita o PIN de novo).
- Validação de que o `waba_id` informado corresponde de fato ao `phone_number_id` (a Meta não expõe isso de forma simples numa única chamada; possível hardening futuro).
- Papéis/permissões diferenciados para quem pode conectar/desconectar (hoje só existe o papel `admin`; pendência já registrada no CLAUDE.md).
