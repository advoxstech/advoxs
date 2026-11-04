# Transcrição de áudio do contato + base de processamento de mídia — design

**Data:** 2026-11-04
**Responsável:** Falcão (estrutura e áudio) · consumidor do contrato: João (PDF/OCR)
**Branch:** `feature/audio-transcricao`

## 1. Objetivo e critério de aceite

Um áudio enviado pelo contato no WhatsApp (Meta ou Z-API) vira texto, é
vinculado à mensagem e à conversa corretas, respeita a sequência do atendimento
e gera **uma única** resposta do agente.

**Aceite:** nos casos suportados, a transcrição preserva o sentido e os fatos
essenciais, chega à conversa certa e gera uma única resposta. Trechos incertos
geram pedido de confirmação ao contato, nunca fatos completados.

Regras inegociáveis: isolamento por tenant, nenhum conteúdo sensível em log,
recuperação após falha, idempotência em eventos repetidos.

## 2. Decisões tomadas

| Tema | Decisão |
|---|---|
| Provedor | API da OpenAI, modelo `gpt-4o-transcribe` (já é dependência do projeto). |
| Quando transcrever | Modo `agent` **e** modo `human` (inclui contatos `human_only`). Não transcreve no billing gate nem quando o turno seria custeado pelo tenant com saldo `<= 0`. |
| Limite de duração | 10 minutos (`AUDIO_MAX_DURATION_SECONDS=600`, ajustável). |
| Limite de tamanho | 20 MB (`attachment_max_bytes` atual). |
| Onde vive o estado | Tabela nova `message_media` (1 linha por mensagem com mídia), compartilhada com o OCR. |
| Onde roda | Etapa `prepare_media` dentro de `_process_inbound_message` (worker), antes de qualquer saída antecipada. Sem job Arq separado. |
| Normalização | Nenhuma (sem ffmpeg). Formatos fora da lista aceita viram `invalid_format`. |

## 3. Fluxo

Estado atual: webhook (`api`) persiste `messages` (`media_url` = media_id da Meta
ou URL da Z-API, `media_type` = mime) → `inbound_message_jobs` → Arq →
`_process_inbound_message` (`apps/worker/app/tasks/messages.py`). Hoje áudio cai
em `process_inbound_attachment` e vira a nota "formato não suportado".

Novo fluxo (`app/tasks/media.py::prepare_media`, chamado logo após
`_load_context`):

1. Classifica a mídia pelo `media_type` **sem parâmetros** (`audio/ogg; codecs=opus`
   → `audio/ogg`). `audio/*` → `kind=audio`. Documentos continuam no caminho
   atual (`process_inbound_attachment`), sem mudança.
2. Áudio com conversa em `billing_gate`, ou com saldo do tenant `<= 0` sem turno
   custeado pelo cliente final / assinatura ativa → grava `status=skipped`
   (`error_code=billing_gate` | `no_credits`) e segue o fluxo atual.
3. Linha `message_media` já em `done` → reaproveita `text` (sem download, sem
   chamada à OpenAI, sem novo débito). Em `failed` com erro permanente, ou com
   erro transitório e `attempts >= 3` → reaproveita a nota de falha. Em `failed`
   transitório com `attempts < 3` → tenta de novo (passo 4).
4. Caso contrário: `status=processing`, `attempts += 1`, `started_at` →
   download autenticado (`download_meta_media` / `download_zapi_media`, já
   existentes) → validação (§5) → transcrição (§6) → `done` ou `failed`,
   `finished_at`. Commit **antes** de chamar o agents.
5. O texto **não** sobrescreve `messages.content` (que guarda a legenda original,
   em geral vazia). É usado para montar:
   - a mensagem enviada ao agents em modo `agent`;
   - o texto sincronizado no checkpoint (`_sync_context`) em modo `human`, saldo
     esgotado e `human_only` — a IA conhece o conteúdo quando a conversa voltar.
6. Sequência: a trava por conversa (`conversation_processing_locks`) e a checagem
   de mensagem mais nova já existentes continuam valendo — um texto enviado depois
   do áudio só é processado depois da transcrição.

### Formato da nota para o agente

Sucesso:
```
[Áudio do cliente, transcrito automaticamente]: <texto>
```
Quando houver trechos incertos, acrescenta:
```
[Trechos entre [? ?] ficaram incertos na transcrição: confirme com o cliente antes de usá-los e não complete fatos.]
```
Falha (exemplo para `silence`):
```
[O cliente enviou um áudio que não pôde ser transcrito (sem fala detectada). Peça que reenvie o áudio ou escreva a mensagem.]
```
Cada `error_code` tem um texto fixo de motivo em linguagem simples.

## 4. Modelo de dados — migration `0049_message_media`

Tabela `message_media` (tenant-scoped, RLS `tenant_isolation`, grants para
`advoxs_app` e `advoxs_system`, mesmo padrão da `0048`):

| Coluna | Tipo | Observação |
|---|---|---|
| `message_id` | uuid PK, FK `messages.id` `ON DELETE CASCADE` | 1:1 com a mensagem |
| `tenant_id` | uuid NOT NULL, FK `tenants.id` | RLS |
| `kind` | text, check `audio` \| `document` \| `image` | |
| `status` | text, check `pending` \| `processing` \| `done` \| `failed` \| `skipped` | |
| `attempts` | integer NOT NULL default 0 | |
| `error_code` | text nullable, check na lista do §7 | |
| `text` | text nullable | transcrição (ou texto do OCR) |
| `uncertain_spans` | jsonb nullable | `[{"text": "...", "confidence": 0.41}]` |
| `duration_seconds` | numeric(8,2) nullable | |
| `provider` | text nullable | ex: `openai:gpt-4o-transcribe` |
| `cost_usd` | numeric(10,6) nullable | |
| `credits_consumed` | numeric(12,4) nullable | |
| `started_at`, `finished_at`, `created_at`, `updated_at` | timestamptz | |

Índice `(tenant_id, status)`.

`pricing_configs` ganha a coluna nullable `transcription_credits_per_minute`
(numeric(8,4)). A migration **insere uma nova linha** copiando os valores
vigentes e definindo `transcription_credits_per_minute = 1.0` (valor inicial,
sujeito à calibração: custo ~US$ 0,006/min ≈ 0,33 crédito/min a R$ 0,10 por crédito).
Linhas existentes não são editadas.

Teste de migration: dump local (`pg_dump`) → `upgrade` → `downgrade` → `upgrade`
→ restauração do dump; procedimento documentado em
`docs/audio-transcricao-operacao.md`.

## 5. Validação

Antes de qualquer chamada à OpenAI:

- **Tipo real por magic bytes** (não confia no mime declarado). Aceitos:
  OGG/Opus (`OggS`), MP3 (`ID3` ou frame sync `0xFFE`), M4A/MP4 (`ftyp` no
  offset 4), WAV (`RIFF`…`WAVE`), WebM (`1A 45 DF A3`). Qualquer outro (ex: AMR
  `#!AMR`) → `invalid_format`.
- **Tamanho** > 20 MB → `too_large` (o download já interrompe no limite).
- **Duração** via `mutagen` (Python puro). Ilegível → `invalid_format`; acima de
  `AUDIO_MAX_DURATION_SECONDS` → `too_long`.

## 6. Transcrição

- `app/clients/transcription.py::transcribe(audio_bytes, filename) -> TranscriptionResult`
  (`text`, `uncertain_spans`, `duration_seconds`).
- OpenAI `audio.transcriptions.create(model="gpt-4o-transcribe", language="pt",
  response_format="json", include=["logprobs"])`, timeout 120 s, chave
  `OPENAI_API_KEY` nova no `.env` do worker (validada no startup em produção).
- **Incerteza:** tokens agrupados em palavras; palavra com probabilidade
  (`exp(logprob)`) abaixo de `TRANSCRIPTION_UNCERTAIN_THRESHOLD=0.5` é marcada
  `[?palavra?]` no texto (palavras incertas adjacentes formam um único trecho) e
  registrada em `uncertain_spans`.
- **Silêncio:** texto vazio ou sem nenhuma letra → `silence`.
- **Concorrência:** `asyncio.Semaphore(TRANSCRIPTION_MAX_CONCURRENCY=2)` por
  processo do worker.
- **Custo:** `cost_usd = duration_seconds / 60 × TRANSCRIPTION_USD_PER_MINUTE`
  (env, default `0.006`); `credits_consumed = duration_seconds / 60 ×
  transcription_credits_per_minute`, `ROUND_HALF_UP` para inteiro (mesma regra
  de `calcular_creditos`).

## 7. Erros (contrato fechado, compartilhado com o OCR)

| `error_code` | Classe | Comportamento |
|---|---|---|
| `download_failed` | transitória | retry do job (até 3 tentativas de mídia, contadas em `attempts`), depois `failed` |
| `timeout` | transitória | idem |
| `provider_unavailable` | transitória (5xx, 429, erro de conexão) | idem |
| `invalid_format` | permanente | `failed` imediato |
| `too_large` | permanente | `failed` imediato |
| `too_long` | permanente | `failed` imediato |
| `silence` | permanente | `failed` imediato |
| `no_credits` | não processado | `skipped` |
| `billing_gate` | não processado | `skipped` |

Erro transitório na tentativa < 3: grava `failed` provisório com `error_code` e
deixa o job Arq refazer (`Retry` já existente; nada foi enviado ao contato ainda).
Na 3ª tentativa, segue com a nota de falha — o atendimento nunca fica travado.

**Aviso ao contato:** em modo `agent`, a nota de falha vai para o agente, que
responde no turno normal (uma resposta). Em modo `human`, nenhuma mensagem
automática; o painel mostra o selo "Áudio não transcrito" com o motivo.

## 8. Cobrança

- Modo `agent`: créditos da transcrição somam ao débito do turno, na mesma
  carteira que paga a resposta (tenant ou cliente final — moeda única); turno
  com assinatura ativa do cliente final não debita nada, como hoje.
- Modo `human` / `human_only`: lançamento `consumption` próprio no ledger do
  tenant (`related_message_id` = mensagem do áudio, descrição sem a palavra
  "tokens"), com o mesmo `FOR UPDATE` da wallet.
- Reaproveitamento de `done` nunca debita de novo.

## 9. Privacidade e retenção

- Logs: apenas IDs (tenant, conversa, mensagem), `kind`, `status`, `error_code`,
  `attempts`, `duration_seconds`, `download_ms`, `transcribe_ms`, `cost_usd`.
  Nunca texto, trecho incerto ou URL de mídia. Exceções logadas por `safe_error`.
- Áudio só em memória; nunca gravado em disco — não há temporário a limpar.
- A transcrição segue o ciclo de vida da mensagem (cascata ao excluir a conversa).
  O áudio original permanece no provedor, como hoje.

## 10. Operação

- **Limites da VPS:** `deploy.resources.limits` (CPU/memória) no
  `docker-compose.yml` para `worker`, com valores definidos pela medição de carga
  pequena (Etapa 4). Valores iniciais propostos: worker `1.0` CPU / `768M`.
- **Métricas:** uma linha de log estruturada por mídia (campos do §9). Tempo
  médio e taxa de falha por query em `message_media` (`started_at`/`finished_at`,
  `status`, `error_code`) — queries documentadas em
  `docs/audio-transcricao-operacao.md`.
- **Recuperação:** a recuperação de jobs existente (`recover_inbound_message_jobs`,
  lease de 15 min) refaz o job; uma linha presa em `processing` com lease vencido
  é tratada como nova tentativa.

## 11. Painel

`MessageOut` (`apps/api/app/schemas/conversations.py`) ganha `media:
{kind, status, error_code, text} | null`. A bolha da mensagem do contato mostra a
transcrição (rótulo "Transcrição automática") abaixo do download do anexo, ou o
selo "Áudio não transcrito — <motivo>". Trechos `[? ?]` ficam destacados.

## 12. Testes

- **Unitários (worker):** magic bytes por formato; duração (`too_long`,
  ilegível); mime com parâmetros; mapeamento de exceções para `error_code`;
  reaproveitamento de `done` sem chamada à OpenAI nem débito; retry transitório
  até 3; marcação de incerteza; silêncio; `skipped` no billing gate e sem saldo;
  transcrição em modo `human` sincronizada no checkpoint; logs sem conteúdo.
- **Integração:** RLS de `message_media` contra Postgres real (padrão
  `test_rls_isolation.py`); migration upgrade/downgrade.
- **Casos reais (Etapa 4, ≥ 10):** áudio curto; próximo ao limite; ruído; voz
  baixa; silêncio; números e valores; nomes próprios; formato aceito; formato
  inválido; sequência com textos. Mais: evento repetido (mesmo `wa_message_id` e
  retry do job) → uma transcrição, uma resposta, um débito; acesso entre tenants.
  Resultados registrados em `docs/audio-transcricao-operacao.md`.

## 13. Contrato para o PDF/OCR (João)

- Gravar o resultado em `message_media` com `kind=document` ou `image`, mesmos
  `status`, transições (`pending → processing → done|failed|skipped`) e
  `error_code` do §7 (novos códigos só por acordo, com alteração do check).
- `message_id` é a chave de idempotência; `done` nunca é reprocessado
  automaticamente.
- Texto para o agente sempre no formato `[<origem>]: <texto>`, com a mesma regra
  de marcação `[? ?]` para baixa confiança.
- Toda leitura/escrita via sessão tenant-scoped (`open_tenant_session`); ingestão
  no RAG continua com `doc_id = message_id` e `tenant_id` obrigatório.
- Mesmas regras de log do §9.

## 14. Fora de escopo

AMR e normalização via ffmpeg; reprocessamento manual pelo painel; dashboard de
métricas; transcrição de áudio enviado pelo atendente; áudio em conversas de
teste; alteração do prompt base dos agentes.
