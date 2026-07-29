# Conexão de WhatsApp via Z-API — Design

## Contexto

Hoje o único jeito de um escritório conectar o WhatsApp é via WhatsApp Business Cloud API oficial (Meta): criar um app, um System User, gerar token permanente, verificar o número — um processo burocrático que exige aprovação como negócio pela Meta. Isso é uma barreira real de entrada pra escritórios pequenos, que só querem usar o número de WhatsApp que já têm.

A **Z-API** é um provedor brasileiro não-oficial que expõe uma API REST própria sobre uma sessão de WhatsApp Web — conexão por QR code, sem aprovação de negócio, sem WABA. É bem mais simples de configurar, ao custo de não ser a via oficial suportada pela Meta (risco de banimento do número por parte da própria Meta é responsabilidade do escritório, não nosso — mesma lógica de qualquer integração não-oficial de WhatsApp).

O objetivo desta entrega: permitir que o escritório escolha, na hora de conectar, entre a via oficial (Meta, como hoje) ou a Z-API — sem duplicar nada da lógica de conversas, agentes ou billing, que já é agnóstica de canal a partir do momento em que a mensagem está persistida em `messages`.

## Pesquisa — como a Z-API funciona (confirmado contra a documentação oficial, `developer.z-api.io`)

- **Autenticação**: `instanceId` + `token`, embutidos na própria URL de cada chamada (`https://api.z-api.io/instances/{instanceId}/token/{token}/...`). Opcionalmente, um `Client-Token` (header `Client-Token`) como camada extra de proteção da própria Z-API — não obrigatório, mas recomendado.
- **Conexão do número**: por QR code (like WhatsApp Web), não por aprovação de negócio. `GET .../qr-code-image` devolve a imagem do QR code já em base64, pronta pra exibir. `GET .../status` devolve `{connected: bool, smartphoneConnected: bool, error: string}`. ⚠️ Existe um fluxo alternativo de "passkey challenge" (WebAuthn-like) que a Z-API às vezes exige em vez do QR code puro, pra números mais novos — não coberto nesta v1 (ver Fora de escopo).
- **Envio de texto**: `POST .../send-text`, corpo `{"phone": "5511999999999", "message": "..."}` (telefone só dígitos, com DDI). Resposta `{"zaapId", "messageId", "id"}`.
- **Webhook de mensagem recebida**: 1 URL de callback por instância, configurável tanto pelo dashboard da Z-API quanto **programaticamente via API** (`POST .../webhooks`, corpo `{"value": "<url>", "notifySentByMe": bool}`) — diferente da Meta, dá pra configurar isso pra o tenant automaticamente, sem pedir pra ele colar nada em lugar nenhum. O payload de cada mensagem recebida inclui `instanceId` (chave de resolução, equivalente ao `phone_number_id` da Meta), `phone` (remetente), `messageId` (dedup), `text.message` (conteúdo), `fromMe` (bool — mensagem enviada pelo próprio WhatsApp Web conectado, não pelo contato).
- **Sem assinatura HMAC**: diferente do `X-Hub-Signature-256` da Meta, a Z-API não documenta nenhum mecanismo de assinatura pra provar que um POST no webhook é legítimo. Isso muda a estratégia de segurança do endpoint (ver seção Segurança).
- **Desconectar**: `POST .../disconnect`.
- Exatidão de headers vs. path pra cada endpoint específico (a doc mistura os dois padrões em trechos diferentes) precisa ser reconfirmada linha a linha na hora de implementar cada chamada — a pesquisa desta spec confirma que os recursos existem e o formato geral, não fecha os detalhes byte-a-byte de cada request.

## Escopo

1. Um tenant pode conectar o WhatsApp via **Meta** (como hoje, inalterado) OU via **Z-API** — nunca os dois ao mesmo tempo, continua sendo 1 número por tenant.
2. Fluxo de conexão Z-API: colar `instanceId`/`token`/`Client-Token` opcional → validação → configuração automática do webhook → exibição do QR code → polling até parear → status "conectado".
3. Webhook de entrada da Z-API, mapeando pro mesmo pipeline de persistência (`conversations`/`messages`) e enfileiramento (Arq) já existente.
4. Envio de resposta (agente ou humano) via Z-API quando for o provedor do tenant, com a mesma interface que o `WhatsAppClient` (Meta) já expõe hoje.
5. Painel (`/configuracoes/whatsapp`) com escolha de provedor e formulário específico pra cada um.

Fora de escopo (ver seção dedicada no fim): troca de provedor com migração automática de estado, paridade de mídia rica (botões/listas/áudio), fluxo de passkey challenge, provisionamento de instância Z-API em nome do tenant (o tenant cria a própria conta/instância na Z-API, fora da nossa plataforma — só conectamos com o que ele já tem, mesmo modelo de "cola as credenciais" já usado pra Meta).

## Modelo de dados

### `whatsapp_numbers` — ganha um discriminador de provedor (migration nova)

Mesmo padrão já usado em `tenant_billing_settings.billing_provider` — uma coluna discriminadora numa tabela só, em vez de tabelas separadas por provedor (evita duplicar toda a lógica de "1 número por tenant"/conexão/desconexão em dois lugares).

| Coluna | Mudança |
|---|---|
| `provider` | **Nova** — `String`, `NOT NULL`, `server_default='meta'` (backfill de todas as linhas existentes) |
| `phone_number_id` | Fica **nullable** (só obrigatório pra `provider="meta"`); mantém `UNIQUE`, mas o `UNIQUE` de coluna nullable no Postgres já permite múltiplos `NULL` sem conflito — sem problema pra linhas Z-API |
| `waba_id` | Fica **nullable** (só Meta) |
| `zapi_instance_id` | **Nova** — `String`, nullable, `UNIQUE` (chave de resolução do webhook Z-API, equivalente ao `phone_number_id`) |
| `zapi_instance_token_encrypted` | **Nova** — `Text`, nullable, cifrado (mesmo Fernet/chave já usada por `access_token_encrypted`) |
| `zapi_client_token_encrypted` | **Nova** — `Text`, nullable, cifrado — `Client-Token` é opcional na própria Z-API, então esta coluna pode ficar `NULL` mesmo com `provider="zapi"` |
| `zapi_webhook_secret` | **Nova** — `String`, nullable, gerado por nós (`secrets.token_urlsafe`) no momento da conexão — nunca cifrado (não é credencial da Z-API, é só um valor aleatório nosso que compõe a URL do webhook) |

`access_token_encrypted` (Meta) e as 4 colunas novas de Z-API ficam todas nullable — cada linha só preenche o bloco do seu próprio `provider`. Um `CHECK` constraint garante consistência: `provider='meta'` exige `phone_number_id`/`waba_id`/`access_token_encrypted` não-nulos; `provider='zapi'` exige `zapi_instance_id`/`zapi_instance_token_encrypted`/`zapi_webhook_secret` não-nulos (`zapi_client_token_encrypted` continua livre, já que o `Client-Token` é opcional na própria Z-API). Como a rota de conexão (ver Backend) só persiste a linha depois de validar as credenciais **e** configurar o webhook com sucesso — mesmo princípio de "nada é salvo se a validação externa falhar" já usado em `/whatsapp/connect` —, `zapi_webhook_secret` está sempre preenchido no momento em que a linha passa a existir; não há janela intermediária com o campo nulo.

## Backend (`apps/api`)

### Conectar via Z-API — `POST /api/v1/whatsapp/connect-zapi`

Rota nova, separada de `/whatsapp/connect` (que continua 100% Meta, sem nenhuma mudança) — mesmo raciocínio já aplicado ao Stripe Connect (`/end-customer-billing/connect-account` separado do formulário `PATCH /settings` do modelo antigo): os dois provedores têm passos de validação completamente diferentes, misturar tudo numa rota só criaria condicionais confusas.

1. Recebe `{instance_id, instance_token, client_token: str | None}`.
2. Valida chamando `GET .../status` da Z-API com essas credenciais — só precisa responder (mesmo que `connected=false`, o número ainda não foi pareado), erro de autenticação vira `400`.
3. Gera `zapi_webhook_secret` (`secrets.token_urlsafe(32)`) e chama `POST .../webhooks` da Z-API pra configurar `{api_public_url}/api/v1/webhooks/zapi/{zapi_webhook_secret}` como callback (`notifySentByMe=false` — não precisamos de eco das mensagens que nós mesmos mandamos, o `agents` já sabe o que respondeu).
4. Persiste a linha em `whatsapp_numbers` (`provider="zapi"`, credenciais cifradas, `status="disconnected"` — só vira `"connected"` depois do QR pareado, ver próximo endpoint) — nada é salvo se os passos 2/3 falharem.

### Status de pareamento — `GET /api/v1/whatsapp/zapi-status`

Endpoint de polling (mesmo padrão de "resolver estado assíncrono" já usado no self-heal do Stripe Connect desta mesma sessão): consulta `GET .../status` na Z-API ao vivo, e se `connected=true` e `whatsapp_numbers.status` ainda não é `"connected"`, atualiza pra `"connected"` — assim a UI só precisa dar polling nesse endpoint até ele confirmar, sem duplicar a lógica de decidir "está pronto" em dois lugares.

### QR code — `GET /api/v1/whatsapp/zapi-qrcode`

Proxy fino pro `GET .../qr-code-image` da Z-API — devolve a imagem base64 pro frontend renderizar. Não persiste nada.

### `GET /whatsapp/connection` e `POST /whatsapp/disconnect` — generalizados

`WhatsAppConnectionOut` ganha o campo `provider`. `disconnect` passa a ramificar: `provider="zapi"` chama `POST .../disconnect` da Z-API antes de marcar `status="disconnected"` no banco (mesmo princípio de "avisar o provedor externo antes de desconectar localmente" — hoje o disconnect da Meta só marca o status local, sem chamar a Graph API; mantenho essa assimetria porque a Meta não tem um endpoint de "desconectar" equivalente, só de desinscrever webhook, fora de escopo).

### Webhook de entrada — `POST /api/v1/webhooks/zapi/{webhook_secret}`

Rota nova, nunca reaproveitando `/webhooks/whatsapp` (que fica 100% intocada — não posso arriscar quebrar callbacks já configurados em apps Meta reais de tenants em produção). `{webhook_secret}` no path é a única camada de autenticação (a Z-API não assina o payload) — comparação em tempo constante (`hmac.compare_digest`) contra `whatsapp_numbers.zapi_webhook_secret`; segredo desconhecido ou tenant não encontrado devolve `200` genérico (mesmo padrão anti-enumeração já usado no webhook da Stripe por tenant). Resolve o tenant via `instanceId` do payload (`WhatsAppNumber.provider='zapi' AND zapi_instance_id=<instanceId>`) — o `webhook_secret` no path já basta pra granularidade de tenant, mas o `instanceId` é a chave "de negócio" real caso um dia existam múltiplas instâncias por tenant (não é o caso hoje, mas é de graça manter a resolução correta por esse campo em vez de confiar só no path).

Ignora silenciosamente (200) eventos com `fromMe=true` (mensagem mandada pelo próprio WhatsApp Web conectado, não uma mensagem de um contato) e mensagens sem `text.message` (por ora, mídia recebida via Z-API não é processada — mesma limitação de mídia inbound já documentada pra Meta hoje, "download de mídia é pendência").

Mapeamento pro `InboundWhatsAppMessage` já existente (`extract_inbound_messages`/`_persist_inbound_message` em `app/services/whatsapp_inbound.py` continuam genéricos — só ganham uma função de extração nova, `extract_inbound_zapi_message`, com o mesmo formato de retorno): `phone` → `contact_phone_number`, `messageId` → `wa_message_id`, `text.message` → `content`.

## Backend (`apps/worker`)

`_load_context` (em `apps/worker/app/tasks/messages.py`) passa a selecionar também `provider`, `zapi_instance_id`, `zapi_instance_token_encrypted`, `zapi_client_token_encrypted` de `whatsapp_numbers`. Na hora de montar a chamada pro `agents`, decripta o bloco certo conforme `provider` e passa os campos correspondentes em `send_message_to_agents` — a assinatura dessa função ganha `whatsapp_provider`/`zapi_instance_id`/`zapi_token`/`zapi_client_token` como parâmetros novos (opcionais, vazios quando `provider="meta"`), espelhando o padrão de `phone_number_id`/`access_token` que já existe.

## Backend (`apps/agents`)

### Contrato `POST /messages` — `IncomingMessage` ganha o discriminador

```python
whatsapp_provider: Literal["meta", "zapi"] = "meta"
zapi_instance_id: str = ""
zapi_token: str = ""
zapi_client_token: str = ""
```

Continua retrocompatível: qualquer chamador que não mande esses campos novos (`send_to_whatsapp=False` do playground, ou uma chamada antiga) cai em `"meta"` por padrão, comportamento idêntico ao de hoje.

### `clients/zapi.py` — novo cliente, mesma interface do `WhatsAppClient`

`ZApiClient(instance_id, token, client_token: str | None)` implementa `send_text_message(to, text)` e `send_document_message(to, link, filename, caption)`, devolvendo o mesmo formato `{"success": bool, "data": ..., "error": ...}` que `WhatsAppClient` já devolve — quem chama (`api/routes.py`) não precisa saber qual dos dois está por trás.

**Refatoração pequena e justificada**: hoje toda a lógica de retry (3 tentativas, backoff, distinção 4xx-não-retry vs 5xx-retry) e rate limiting (`acquire_rate_limit_slot`) mora dentro de `WhatsAppClient._safe_request` — duplicá-la inteira dentro de `ZApiClient` seria ~130 linhas repetidas. Extraio esse miolo pra um helper compartilhado (`clients/http_retry.py`, função livre parametrizada por client HTTP/rate-limit-key/nome do serviço pra log) que os dois clientes passam a chamar. Escopo da extração: só o que já existe e já seria duplicado — nenhuma feature nova de retry/rate-limit.

`api/routes.py`: o bloco `async with WhatsAppClient(body.phone_number_id, body.access_token) as client:` vira uma pequena função `_build_whatsapp_client(body)` que devolve `WhatsAppClient(...)` ou `ZApiClient(...)` conforme `body.whatsapp_provider` — resto do bloco (loop de envio, contagem de falhas) fica idêntico, porque os dois clientes têm a mesma interface.

## Frontend (`apps/web`)

`/configuracoes/whatsapp` (`WhatsAppConnectionPanel.tsx`): quando não há número conectado ainda, mostra um seletor de provedor ("WhatsApp Business oficial" vs "Z-API") antes de qualquer formulário — decisão só relevante nesse momento inicial, já que depois de conectado o painel simplesmente mostra o status (`Conectado via Z-API` / `Conectado via WhatsApp Business oficial`, `WhatsAppConnectionOut.provider` novo) e o botão de desconectar, igual pros dois provedores.

Fluxo Z-API: formulário (`instance_id`, `instance_token`, `client_token` opcional) → submit chama `connect-zapi` → em caso de sucesso, mostra o QR code (`GET zapi-qrcode`, `<img src="data:image/png;base64,...">`) com polling em `GET zapi-status` (mesmo padrão de intervalo/estado já usado em outros polling deste painel, ex. `/creditos/sucesso`) até `status="connected"` — nesse momento, esconde o QR e mostra a tela final de conectado.

## Segurança

- Credenciais Z-API cifradas em repouso com o mesmo Fernet já usado pro token da Meta (`WHATSAPP_TOKEN_ENCRYPTION_KEY`) — nenhuma chave nova de env necessária.
- O segredo do webhook (`zapi_webhook_secret`) nunca é exposto no frontend nem em nenhuma resposta de API — é gerado no backend, usado só na chamada servidor-a-servidor de configuração do webhook e guardado em texto plano no banco (não é uma credencial da Z-API, é só um token nosso; comparação em tempo constante no momento da validação, mesmo tratamento de qualquer outro segredo comparado neste código-base).
- `Client-Token`, quando informado, é enviado em toda chamada à Z-API (header `Client-Token`) — camada extra de proteção do lado deles, opcional, mas repassada sempre que presente.

## Fora de escopo

- Troca de provedor com migração de estado (desconectar + reconectar do zero já resolve, sem necessidade de portar nada).
- Paridade de mídia rica da Z-API (botões, listas interativas, áudio) — só texto e documento, o que o `agents` já usa hoje.
- Fluxo de "passkey challenge" no lugar do QR code — erro claro direcionando o tenant pro próprio painel da Z-API, sem UI dedicada.
- Provisionamento de instância Z-API em nome do tenant — ele cria a própria conta/instância na Z-API, exatamente como hoje ele cria o próprio App/System User na Meta.
- Suporte a mais de uma instância Z-API por tenant.
