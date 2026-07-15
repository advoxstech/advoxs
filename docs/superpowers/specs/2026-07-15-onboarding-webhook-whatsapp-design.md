# Onboarding do WhatsApp — subscribed_apps automático + instruções de webhook

**Data:** 2026-07-15
**Status:** aprovado

## Problema

Teste real em produção travou em dois buracos do onboarding manual do WhatsApp:

1. Mesmo com o número conectado, webhook verificado e o campo `messages` assinado, nenhuma
   mensagem chega se o app da Meta do tenant não estiver inscrito na WABA
   (`POST /{waba_id}/subscribed_apps`). A falha é silenciosa: tudo parece funcionar e nada chega.
2. A página `/configuracoes/whatsapp` pede as credenciais mas não menciona o passo manual do
   webhook (URL + verify token + assinar `messages` no painel da Meta) — todo cliente trava aí.

## Solução

### 1. `subscribed_apps` automático no connect

- Nova função em `apps/api/app/clients/whatsapp.py`:
  `subscribe_app_to_waba(waba_id: str, access_token: str) -> None` —
  `POST {graph_api_base_url}/{graph_api_version}/{waba_id}/subscribed_apps` com
  `Authorization: Bearer`. Mesma taxonomia de erro das funções vizinhas:
  `httpx.HTTPError` → `WhatsAppNetworkError`; resposta de erro → `WhatsAppApiError` com a
  mensagem extraída por `_meta_error_message` (fallback: "Não foi possível inscrever o app na
  WhatsApp Business Account — confira o WABA ID").
- Chamada no `POST /api/v1/whatsapp/connect` **depois** de `register_number` e **antes** de
  persistir qualquer coisa (mantém a garantia "nada é salvo se a Meta rejeitar").
- Falha **bloqueia** o connect, com o mesmo mapeamento das etapas anteriores:
  `WhatsAppApiError` → 400 (detail da Meta), `WhatsAppNetworkError` → 502 (detail genérico).
- Idempotente do lado da Meta (inscrever app já inscrito → success) — reconexão/troca de número
  não precisa de tratamento especial.

### 2. Instruções de webhook na página

- Novo endpoint `GET /api/v1/whatsapp/webhook-config` (autenticado via `get_current_tenant`;
  não toca em tabela tenant-scoped, só lê config):
  `{"callback_url": "<API_PUBLIC_URL>/api/v1/webhooks/whatsapp", "verify_token": "<META_VERIFY_TOKEN>"}`.
  - Racional de ser endpoint (e não `NEXT_PUBLIC_*`): o verify token já vive no env do `api`
    (fonte única), e envs `NEXT_PUBLIC_*` são congeladas no build da imagem no CI — não
    funcionam para valores por ambiente.
- Env nova no `api` (`app/core/config.py`): `api_public_url: str = ""` (`API_PUBLIC_URL`).
  Se vazia, `callback_url` degrada para o path relativo `/api/v1/webhooks/whatsapp` (dev local).
- UI em `WhatsAppConnectionPanel.tsx`: seção "Configurar webhook na Meta" com passo a passo
  numerado (painel do app → WhatsApp → Configuration → Webhook), os dois valores em campos
  read-only com botão "Copiar" (feedback "Copiado!" temporário), e a instrução de assinar o
  campo `messages` em Webhook fields. Visível no modo formulário **e** com número conectado.
- O painel busca `whatsapp/webhook-config` via `backendFetch` no mesmo load da conexão;
  se a chamada falhar, a seção simplesmente não renderiza (não bloqueia o resto da página).

## Testes

- Unit (`api`): endpoint novo devolve os valores de settings (com e sem `API_PUBLIC_URL`);
  `connect` chama `subscribe_app_to_waba` com `(waba_id, access_token)`;
  `WhatsAppApiError` na inscrição → 400 e nada persistido;
  `WhatsAppNetworkError` → 502 e nada persistido.
- Unit (`web`): painel renderiza a seção de instruções com os valores do msw handler novo;
  botão copiar usa `navigator.clipboard.writeText`.

## Documentação

- CLAUDE.md, seção "Integração WhatsApp Business": registrar o `subscribed_apps` automático no
  connect, as instruções de webhook na página e a env `API_PUBLIC_URL`.

## Fora de escopo

- Validação de assinatura `X-Hub-Signature-256` por tenant (App Secret por tenant em
  `whatsapp_numbers`) — pendência separada, registrada em CLAUDE.md.
- Automatizar a configuração do webhook do app (impossível sem acesso ao app do tenant).
