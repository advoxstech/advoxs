# Design — Perfil do Escritório (`/perfil`)

Data: 2026-07-09
Status: aprovado

## Objetivo

Página `/perfil` no painel do tenant: nome do escritório editável, logo (upload), troca de senha do usuário logado, e um botão "Sair da conta" bem visível. Fecha o pacote de melhorias pedido pelo usuário junto com a Feature B (switch de IA + resumo de conversa, spec separado).

## Decisões de produto

- **Nome do escritório editável** — `PATCH` simples em `tenants.name`.
- **Logo com upload** — PNG/JPG até 2 MB, substitui o monograma "A." na `TenantNav` quando presente.
- **Troca de senha exige a senha atual** — mesma prática de segurança de qualquer troca de senha autenticada; nova senha com a mesma regra do cadastro (mín. 8 caracteres).
- **Botão "Sair" já existe** na `TenantNav` (rodapé, texto vertical) — não é duplicado como funcionalidade nova, mas o perfil ganha um botão "Sair da conta" mais visível/explícito, reaproveitando a mesma Server Action `logout` já existente.
- **Sem verificação de e-mail nem troca de e-mail nesta entrega** — o `email_contato` do tenant e o e-mail de login do usuário não são editáveis aqui (fora de escopo, mesma decisão já tomada no cadastro self-service).

## Débito técnico corrigido nesta entrega: proxy binário

O proxy autenticado do `web` (`/api/backend/[...path]/route.ts`) hoje lê a resposta do `api` com `response.text()` antes de repassar ao browser — isso corrompe qualquer resposta binária (já registrado como débito conhecido em feature anterior). Servir a logo via `<img src="/api/backend/profile/logo">` exige que o proxy repasse bytes crus (`response.arrayBuffer()` + `content-type` preservado) em vez de `text()`. Esse fix faz parte desta entrega, escopado ao proxy de tenant (o proxy de admin não serve arquivo nenhum, fica de fora).

## Modelo de dados

- **Migration nova**: `tenants.logo_filename` (`String`, nullable) — nome do arquivo salvo no volume, sem o `tenant_id` embutido no nome (o path já é escopado por diretório).
- **Volume novo**: `logo_uploads`, mesmo padrão do `kb_uploads` (compartilhado entre `api`/`web`? não — só o `api` grava e serve, sem worker envolvido, então só o container `api` monta o volume). Arquivo salvo em `{logo_upload_dir}/{tenant_id}.{ext}` — nome fixo por tenant (não versionado; um novo upload sobrescreve o anterior, sem lixo acumulando).

## API (todas as rotas autenticadas com `get_current_tenant` + `get_tenant_session`)

- **`GET /api/v1/profile`** → `{tenant_name, email_contato, has_logo, user_name, user_email}`.
- **`PATCH /api/v1/profile`** — body `{tenant_name}` → atualiza `tenants.name`.
- **`POST /api/v1/profile/password`** — body `{current_password, new_password}`. Verifica `current_password` contra o hash do usuário autenticado (`verify_password`, já existente); `new_password` com `Field(min_length=8)`. Senha errada → `400`. Sucesso → `204`, sem retornar token novo (sessão atual continua válida — trocar senha não invalida o JWT já emitido nesta entrega; fora de escopo revogar sessões antigas).
- **`POST /api/v1/profile/logo`** (multipart) — valida extensão (`.png`/`.jpg`/`.jpeg`) e tamanho (≤ 2 MB, mesmo padrão de erro 413 do `knowledge_base.py`), salva em disco, grava `tenants.logo_filename`. Sobrescreve upload anterior (mesmo tenant, mesmo path fixo).
- **`GET /api/v1/profile/logo`** — serve o arquivo (`FileResponse`, `content-type` pelo mime da extensão). `404` se o tenant não tiver logo.

## Frontend (`web`)

- **Página `/perfil`**: item "Perfil" na `TenantNav` (novo, entre "Início" e "Sair" — a nav em si só ganha mais um item, sem mudar os existentes).
- **`ProfilePanel`**: três blocos —
  1. **Dados do escritório**: input do nome (salva via `PATCH`, feedback de sucesso/erro inline) + upload de logo com preview local antes de enviar e preview do que já está salvo (via `<img src="/api/backend/profile/logo">`, com fallback pro monograma se `has_logo=false`).
  2. **Trocar senha**: formulário senha atual + nova senha + confirmação (validação client-side de "senhas não coincidem" antes de enviar).
  3. **Sair da conta**: botão destacado, reaproveita a Server Action `logout` já existente (`@/app/conversas/actions`).
- **`TenantNav`**: quando `has_logo=true`, o monograma "A." é substituído pela `<img>` da logo (busca o `has_logo` uma vez, mesmo padrão do `LowBalanceBanner`). Se a busca falhar, mantém o monograma (fail-safe).
- **Proxy**: allowlist ganha `"profile"`; fix do `response.text()` → `response.arrayBuffer()` no proxy de tenant.

## Erros e casos-limite

| Caso | Comportamento |
|---|---|
| Senha atual incorreta | `400`, mensagem "Senha atual incorreta" |
| Nova senha < 8 caracteres | `422` (validação Pydantic) |
| Logo maior que 2 MB | `413` |
| Extensão não suportada | `400` |
| Tenant sem logo | `GET /profile/logo` → `404`; front mostra o monograma |
| Nome do escritório vazio | `422` (`Field(min_length=1)`, mesma regra do cadastro) |

## Testes

- **api**: `PATCH /profile` atualiza o nome; `POST /profile/password` com senha atual errada → 400, com senha certa → 204 e o hash muda; upload de logo válido/invalido (extensão, tamanho); `GET /profile/logo` 404 sem logo, 200 com o arquivo certo depois do upload; todas as rotas exigem `get_current_tenant`; isolamento por tenant (um tenant não vê/sobrescreve a logo de outro).
- **web**: proxy repassa bytes binários sem corromper (teste com um payload não-UTF-8, ex. um PNG minúsculo); `ProfilePanel` renderiza os 3 blocos, salva nome, mostra erro de senha errada, mostra preview de logo; `TenantNav` mostra a logo quando `has_logo=true` e o monograma quando não.

## Fora de escopo desta entrega

- Verificação/troca de e-mail.
- Revogação de sessões antigas ao trocar a senha.
- Histórico/versionamento de logo.
- Papéis de usuário adicionais (fora de escopo já registrado no CLAUDE.md).
