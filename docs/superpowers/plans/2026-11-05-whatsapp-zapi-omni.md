# Plano — WhatsApp oficial via Z-API Omni (etapa 1)

> Data: 2026-11-05. Status: implementado (falta o teste com um canal Omni real).
> Divergência da implementação: o `agents` **não mudou**. Todo envio de canal Omni sai do
> `api` ou do `worker` (os chamadores usam `send_to_whatsapp=false` e o `worker` entrega pela
> fila de saída), então o contrato de `POST /messages` ficou igual.
> Documentação de referência: https://developer.omni.z-api.io (camada "Compatibilidade Z-API" + API própria).

## Objetivo

Oferecer aos escritórios um número de **WhatsApp oficial (API da Meta)** sem que eles precisem
lidar com a Meta, usando o **Z-API Omni** como intermediário. A Z-API clássica (QR code) e a
conexão Meta direta continuam existindo; o Omni entra como **terceiro provedor** (`omni`).

## Decisões já tomadas

| Decisão | Escolha |
|---|---|
| Conta no Omni | **Um workspace da Advoxs.** Uma Secret Key da plataforma (no `.env`); cada escritório é um **canal** do workspace. |
| Onde o número fica na Meta | **No portfólio empresarial da Advoxs.** O escritório não precisa de conta na Meta; só confirma o código que chega no número. Reversível sem mudar código (migração do número na Meta). |
| Quem conecta | **A Advoxs**, pelo painel do Omni (inclusive em modo coexistência, em chamada com o escritório). No Advoxs, o admin da plataforma só cadastra `channel_id` + token do canal. |
| Conexão pelo próprio escritório (SDK do Omni) | **Fora da etapa 1.** Possível etapa 2, reaproveitando tudo daqui. |

## Como o Omni se encaixa no código atual

A camada de compatibilidade do Omni usa **os mesmos caminhos, corpos e respostas da Z-API**
(`/send-text`, `/send-document/{ext}` …) e entrega webhooks **no mesmo formato da Z-API**
(`payloadFormat: Z_API`). Mudam:

| | Z-API clássica | Omni |
|---|---|---|
| Endereço base | `https://api.z-api.io/instances/{id}/token/{token}` | `https://zapi.omni.z-api.io/channels/{channel_id}/token/{channel_token}` |
| Autenticação | header `Client-Token` (por conta, cifrado por tenant) | header `Authorization: Bearer {OMNI_SECRET_KEY}` (uma chave da plataforma) |
| Webhook | `update-webhook-received` (1 URL) | `POST https://api.omni.z-api.io/v1/channels/{id}/webhooks` (eventos, formato, assinatura HMAC) |
| Lista de opções (`send-option-list`) | existe | **não existe** → botões (até 3) ou texto numerado |
| Mensagem fora da janela de 24 h | livre | **só modelo aprovado** (API própria do Omni) |
| QR code / status / device | rotas da instância | não existem; status por `GET /v1/channels/{id}` (exige função ENTERPRISE) e por webhooks `CONNECTED`/`DISCONNECTED` |

Estratégia: **parametrizar os clientes Z-API existentes** (endereço base + cabeçalhos) em vez de
duplicá-los. O provedor `omni` reaproveita parser de webhook, envio de texto/documento e
retry; só os pontos da tabela acima ganham caminho próprio.

## Mudanças por serviço

### 1. Banco (`apps/api`, migration `0051`)
- `whatsapp_numbers`: colunas novas, todas nulas fora do Omni:
  - `omni_channel_id` (`UNIQUE`), `omni_channel_token_encrypted`,
  - `omni_webhook_id` (para atualizar/remover o webhook no Omni),
  - `omni_webhook_signing_secret_encrypted` (HMAC dos webhooks),
  - `omni_webhook_path_secret` (`UNIQUE`; segredo na URL, mesma camada que a Z-API usa hoje).
- Check constraint `ck_whatsapp_numbers_provider_fields` passa a aceitar `provider = 'omni'` com
  o bloco Omni preenchido. Cifra com a mesma chave Fernet do WhatsApp.
- Modelo SQLAlchemy (`api`) e tabela do `worker` (`app/tables.py`) atualizados.

### 2. Configuração
- `OMNI_ENABLED` (padrão `false`), `OMNI_SECRET_KEY`, `OMNI_COMPAT_BASE_URL`
  (`https://zapi.omni.z-api.io`), `OMNI_API_BASE_URL` (`https://api.omni.z-api.io`) no `api`,
  `worker` e `agents`.
- Validação de produção (`config_validation.py`, mesmo padrão de Meta/Stripe): com
  `OMNI_ENABLED=true`, a Secret Key é obrigatória; com `false`, as rotas Omni respondem 404/409.
- `docs/configuracao-producao.md` atualizado.

### 3. Clientes HTTP
- `api`, `worker` e `agents` (`clients/zapi.py`): uma estrutura de credencial com
  `base_url` + cabeçalhos, montada por provedor (`zapi` ou `omni`). Todas as funções atuais de envio
  passam a recebê-la. Testes existentes da Z-API continuam passando sem mudança de comportamento.
- `api/app/clients/omni.py` (novo, API própria): criar/atualizar/remover webhook do canal, consultar
  canal (`GET /v1/channels/{id}`, tolerando 422 sem ENTERPRISE), listar modelos do WABA e enviar
  modelo (`POST /v1/channels/{id}/messages`, `type: TEMPLATE`).

### 4. Cadastro pelo admin da plataforma
- `POST /api/v1/platform-admin/tenants/{id}/whatsapp/omni` (`channel_id`, `channel_token`,
  `display_phone_number` opcional): consulta o canal (status/número quando houver ENTERPRISE),
  cria o webhook (eventos `MESSAGE_RECEIVED`, `CONNECTED`, `DISCONNECTED`; `payloadFormat: Z_API`;
  `signing: true`) e grava a linha. Reconectar substitui o webhook anterior; trocar de provedor
  limpa os outros blocos (mesmo padrão do Meta ↔ Z-API de hoje).
- Desconectar (admin e tenant): remove o webhook no Omni (melhor esforço) e marca
  `status="disconnected"`. **Não** desconecta o número da Meta.
- Painel do admin (`AdminTenantWhatsAppZApi.tsx`): opção "WhatsApp oficial (Omni)" com o
  formulário acima.
- Painel do escritório (`WhatsAppConnectionPanel.tsx`): quando o provedor é `omni`, mostra
  "Conectado via WhatsApp oficial (gerenciado pela Advoxs)", número e status, sem formulário.

### 5. Recebimento (webhook)
- `POST /api/v1/webhooks/omni/{path_secret}`: resolve o tenant pelo segredo do caminho, valida a
  assinatura HMAC (`x-webhook-signature` sobre `t\ntopic\npartition\noffset\ncorpo`, tolerância de
  5 min, corpo descomprimido se vier em gzip) e responde em menos de 5 s.
- Mensagens: o corpo está no formato Z-API → reaproveita `extract_inbound_zapi_message` e o
  mesmo caminho de persistência/enfileiramento (`whatsapp_inbound.py`), com deduplicação pelo
  id da mensagem.
- Eventos `CONNECTED`/`DISCONNECTED`: atualizam `whatsapp_numbers.status`.
- Resposta de botão (billing gate): o extrator passa a ler também a resposta de botões no
  formato Z-API (`buttonsResponseMessage`), devolvendo o rótulo como texto — mesmo tratamento
  que a lista tem hoje.

### 6. Envio
- `agents` (`POST /messages`): aceita `whatsapp_provider="omni"` (+ id/token do canal); o
  `ZApiClient` usa a credencial Omni. Texto e documento sem mudança de lógica.
- `worker`: `messages.py`, `outbound_outbox.py` (respostas do atendente), `billing_gate.py` e o
  aviso de leitura demorada montam a credencial pelo provedor.
- `api`: `whatsapp_outbound.py` (takeover, isenção, confirmação de pagamento) idem.

### 7. Regras da API oficial
- **Billing gate:** com `omni`, até 3 pacotes → `send-button-list`; acima disso → texto numerado
  ("responda com o número do pacote"), resolvendo pelo número ou pelo nome. Mantém retries e
  escalada para humano.
- **Janela de 24 h:** o aviso do composer que hoje aparece só para `meta` passa a valer para
  `omni`. Respostas do atendente fora da janela continuam bloqueadas pela Meta (sem mudança de
  lógica, só de aviso).
- **Follow-up:** `omni` funciona como `meta` — só modelos aprovados. Listagem e envio pela API
  própria do Omni (`client/omni.py`); a criação de modelos continua fora do painel (no Omni ou
  no Gerenciador da Meta). `FollowUpDialog.tsx` trata `omni` como `meta`.

### 8. Anexos recebidos
- Download (`worker/app/clients/media.py`) e download no painel (`api/app/clients/contact_media.py`):
  incluir o(s) domínio(s) de mídia do Omni na lista permitida e, se exigirem, o `Authorization`
  só para esses domínios. **Domínio e autenticação a confirmar no primeiro teste real.**

## Fora desta etapa
- Conexão pelo próprio escritório (SDK `@omnizapi/connect` + Embedded Signup).
- Criação/gestão de modelos de mensagem pelo painel do Advoxs.
- Respostas enviadas pelo **app do celular** em modo coexistência: verificar no teste real se
  chegam ao webhook (`fromMe`); se chegarem, registrar como mensagem do atendente e pausar a IA
  fica para uma etapa seguinte.
- Migração automática de números que hoje estão na Z-API clássica.

## Testes
- Unitários em `api`, `worker`, `agents` e `web` para cada item acima, incluindo:
  validação da assinatura HMAC (exemplo oficial da documentação como caso de teste), webhook com
  segredo errado, provedor `omni` em cada ponto de envio, billing gate com botões e com texto
  numerado, follow-up com modelo, aviso de 24 h.
- Suítes existentes da Z-API e da Meta continuam passando.
- **Teste real (bloqueia a entrega):** um canal de teste no workspace da Advoxs, com um número de
  teste no portfólio da Advoxs → enviar/receber texto, documento, anexo do cliente, billing gate,
  follow-up com modelo, desconexão. Aproveitar para confirmar domínio dos anexos e o
  comportamento da coexistência.

## Pendências com a Z-API (não bloqueiam o desenvolvimento)
1. Preço por canal e cobrança dos modelos pela Meta.
2. Liberação da função ENTERPRISE para o workspace da Advoxs (consultar canal, assinatura por API).
3. Números de vários escritórios no portfólio da Advoxs: aprovação do **nome de exibição** e se os
   **limites de envio / reputação** contam por número ou pelo portfólio.
4. Domínio e autenticação das URLs de mídia recebida.
5. Mensagens enviadas pelo app em modo coexistência chegam ao webhook?

## Pré-requisitos para o teste real
- Workspace Omni da Advoxs criado; Secret Key gerada.
- Portfólio empresarial da Advoxs na Meta (de preferência verificado) e forma de pagamento para
  modelos.
- Um número de teste (chip) para conectar.

## Estimativa
| Parte | Esforço |
|---|---|
| Banco, configuração, clientes parametrizados | 0,5 dia |
| Cadastro pelo admin + webhook (HMAC) + recebimento | 1 dia |
| Envio em todos os pontos + agents | 0,5 dia |
| Billing gate com botões, aviso de 24 h, follow-up com modelos | 1–1,5 dia |
| Painéis (admin e escritório) | 0,5 dia |
| Teste real e ajustes | 0,5–1 dia |
| **Total** | **4–5 dias** |

Ordem de entrega: 1–6 primeiro (já permite atender por um número Omni), depois 7–8.
