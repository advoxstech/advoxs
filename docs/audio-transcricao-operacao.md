# Transcrição de áudio — operação

## Diagrama
WhatsApp (Meta/Z-API) → api (webhook, persiste `messages`, `inbound_message_jobs`)
→ Arq → worker `_process_inbound_message` → `prepare_media`
(download → validação → OpenAI → `message_media` + débito) → agents | checkpoint | painel.

## Publicação
1. Backup (o `-T` desliga o TTY, sem ele o binário sai corrompido no arquivo do host):
   `docker compose exec -T postgres pg_dump -U advoxs -Fc advoxs > backup-pre-0052.dump`.
2. Deploy normal (`deploy.yml` roda `docker compose run --rm api uv run alembic upgrade head`, aplica a 0052).
3. Conferir (após o upgrade; ver nota de execução das queries em "Métricas"):
   `SELECT count(*) FROM message_media;` (0) e
   `SELECT transcription_credits_per_minute FROM pricing_configs ORDER BY effective_at DESC LIMIT 1;` (1.0000).
4. Definir `OPENAI_API_KEY` e `AUDIO_TRANSCRIPTION_ENABLED=true` no `.env`; `docker compose up -d worker`.
5. Enviar um áudio de teste e conferir a linha `Mídia processada | ... status=done` no log do worker.

## Reversão
- Sem perda de dados: `AUDIO_TRANSCRIPTION_ENABLED=false` + `docker compose up -d worker`
  (o áudio volta ao comportamento anterior).
- Schema: `docker compose stop worker api`, depois
  `docker compose run --rm api uv run alembic downgrade 0051`, depois `docker compose up -d api worker`.
  O downgrade apaga a tabela `message_media` **e** a coluna `pricing_configs.transcription_credits_per_minute`.
  A linha inserida em `pricing_configs` permanece, mas perde esse valor: a query de conferência da Publicação
  só se aplica depois do upgrade.
- Restauração completa (com dados): a restauração exige parar a aplicação, pois o `--clean` derruba objetos em uso.
  1. `docker compose stop api worker`
  2. `docker compose exec -T postgres pg_restore -U advoxs -d advoxs --clean --if-exists < backup-pre-0052.dump`
  3. `docker compose up -d api worker`

  O `-T` aqui também é obrigatório, e o `<` lê o dump do host pela entrada padrão do container.

## Teste de migration com backup (registrar resultado)
dump → `upgrade head` → `downgrade 0051` → `upgrade head` → `pg_restore` num banco descartável.

| Data | Passo | Resultado |
|---|---|---|

## Limites
| Item | Valor | Variável |
|---|---|---|
| Duração | 600 s | AUDIO_MAX_DURATION_SECONDS |
| Tamanho | 25 MB (limite da OpenAI) | AUDIO_MAX_BYTES |
| Tentativas | 3 | MEDIA_MAX_ATTEMPTS |
| Concorrência por processo | 2 | TRANSCRIPTION_MAX_CONCURRENCY |
| Worker (compose) | 1 CPU / 768 MB | docker-compose.yml |

## Métricas (SQL)
Todas as queries desta seção e as de conferência da Publicação rodam no container do Postgres, por exemplo:
`docker compose exec -T postgres psql -U advoxs -d advoxs -c "SELECT count(*) FROM message_media;"`.
Para as queries de múltiplas linhas, use o mesmo comando com o SQL por entrada padrão:
`docker compose exec -T postgres psql -U advoxs -d advoxs < query.sql`.

```sql
-- Tempo médio e taxa de falha, últimos 7 dias
SELECT status, error_code, count(*),
       avg(extract(epoch FROM finished_at - started_at)) AS avg_seconds,
       sum(duration_seconds) / 60 AS audio_minutes, sum(cost_usd) AS cost_usd
FROM message_media
WHERE kind = 'audio' AND created_at > now() - interval '7 days'
GROUP BY status, error_code ORDER BY count(*) DESC;

-- Presos em processamento
SELECT message_id, tenant_id, attempts, started_at FROM message_media
WHERE status = 'processing' AND started_at < now() - interval '15 minutes';
```

## Recuperação
- Worker reiniciado no meio: `recover_inbound_message_jobs` (lease 15 min) refaz o job;
  `processing` órfão vira nova tentativa; `done` nunca é refeito nem recobrado.
- Tentativas transitórias de mídia são limitadas também pelo teto de 5 tentativas do próprio job (`MAX_TRIES`).
- OpenAI fora do ar: 3 tentativas (10 s, 20 s), depois o agente pede para o cliente reenviar/escrever.

## Privacidade
Logs só com IDs, status, error_code, tentativas, duração, tempos e custo. Áudio só em memória.
Transcrição apagada em cascata com a conversa.

## Registro de testes (Etapa 4)
| # | Caso | Arquivo | Esperado | Resultado | Transcrição preserva fatos? | Respostas geradas |
|---|---|---|---|---|---|---|
| 1 | Áudio curto | | done, 1 resposta | | | |
| 2 | Próximo ao limite (~9 min 50 s) | | done | | | |
| 3 | Ruído | | done ou trechos [? ?] | | | |
| 4 | Voz baixa | | done ou trechos [? ?] | | | |
| 5 | Silêncio | | failed/silence, agente pede reenvio | | | |
| 6 | Números e valores | | valores corretos ou [? ?] | | | |
| 7 | Nomes próprios | | nomes corretos ou [? ?] | | | |
| 8 | Formato aceito (mp3/m4a) | | done | | | |
| 9 | Formato inválido (AMR/WebM) | | failed/invalid_format | | | |
| 10 | Sequência áudio + textos | | ordem preservada, 1 resposta | | | |
| 11 | Evento repetido (webhook) | | 1 transcrição, 1 débito | | | |
| 12 | Retry do job após done | | sem nova chamada/débito | | | |
| 13 | Acesso entre tenants | | RLS bloqueia | | | |

## Consumo da VPS (carga pequena)
| Cenário | CPU worker | Memória worker | Tempo médio | Observações |
|---|---|---|---|---|
