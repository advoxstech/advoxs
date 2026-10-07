"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { backendFetch } from "@/lib/client-api";
import type { UrgencyKeyword } from "@/lib/types";

function errorDetail(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export function UrgencyKeywordsPanel({
  agentId,
  agentName,
}: {
  agentId: string;
  agentName: string;
}) {
  const [keywords, setKeywords] = useState<UrgencyKeyword[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoaded(false);
    setKeywords([]);
    setFeedback(null);

    async function load() {
      try {
        const response = await backendFetch(`agents/${agentId}/urgency-keywords`);
        if (cancelled) return;
        if (response.ok) setKeywords(await response.json());
        else setFeedback("Não foi possível carregar as palavras-chave.");
      } catch {
        if (!cancelled) setFeedback("Falha de conexão ao carregar as palavras-chave.");
      } finally {
        if (!cancelled) setLoaded(true);
      }
    }
    void load();

    return () => {
      cancelled = true;
    };
  }, [agentId]);

  async function handleAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const keyword = draft.trim();
    if (!keyword || busy) return;
    setBusy(true);
    setFeedback(null);
    try {
      const response = await backendFetch(`agents/${agentId}/urgency-keywords`, {
        method: "POST",
        body: JSON.stringify({ keyword }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(errorDetail(body, "Não foi possível adicionar a palavra-chave."));
        return;
      }
      setKeywords((current) => [...current, body as UrgencyKeyword]);
      setDraft("");
    } catch {
      setFeedback("Falha de conexão. Tente novamente.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(keyword: UrgencyKeyword) {
    if (busy) return;
    setBusy(true);
    setFeedback(null);
    try {
      const response = await backendFetch(
        `agents/${agentId}/urgency-keywords/${keyword.id}`,
        { method: "DELETE" },
      );
      if (!response.ok && response.status !== 404) {
        setFeedback("Não foi possível remover a palavra-chave.");
        return;
      }
      setKeywords((current) => current.filter((k) => k.id !== keyword.id));
    } catch {
      setFeedback("Falha de conexão. Tente novamente.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRestore() {
    if (busy) return;
    setBusy(true);
    setFeedback(null);
    try {
      const response = await backendFetch(`agents/${agentId}/urgency-keywords/restore-defaults`, {
        method: "POST",
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(errorDetail(body, "Não foi possível restaurar a lista padrão."));
        return;
      }
      setKeywords(body as UrgencyKeyword[]);
    } catch {
      setFeedback("Falha de conexão. Tente novamente.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-4 py-3 text-sm text-danger md:px-8">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-4 py-6 md:px-8">
        <div className="flex max-w-2xl flex-col gap-5">
          <div>
            <h2 className="font-display text-xl font-semibold text-ink">
              Detecção direta de urgência
            </h2>
            <p className="mt-2 text-sm text-muted">
              Mensagens que contenham estas palavras ou frases serão marcadas imediatamente como
              urgentes quando {agentName} estiver responsável pela conversa. A mudança é aplicada
              imediatamente e não depende da publicação de uma versão.
            </p>
            <p className="mt-2 text-sm text-muted">
              A análise contextual de situações críticas feita pela IA continua funcionando
              separadamente, mesmo que esta lista esteja vazia.
            </p>
          </div>

          <form onSubmit={handleAdd} className="flex flex-wrap gap-2">
            <label htmlFor="urgency-keyword" className="sr-only">
              Nova palavra ou frase
            </label>
            <input
              id="urgency-keyword"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              maxLength={60}
              placeholder="Ex: audiência amanhã"
              className="min-w-0 flex-1 rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
            <button
              type="submit"
              disabled={busy || !draft.trim()}
              className="rounded border border-accent bg-accent px-4 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-50"
            >
              Adicionar
            </button>
          </form>

          <p className="text-xs text-muted">
            Maiúsculas e acentos são ignorados. A palavra precisa aparecer inteira: &quot;preso&quot;
            não sinaliza &quot;presos&quot; — adicione as variações que quiser.
          </p>

          <p className="text-xs text-muted" aria-live="polite">
            {keywords.length} de 100 palavras configuradas para este agente.
          </p>

          {!loaded ? (
            <p className="text-sm text-muted">Carregando…</p>
          ) : keywords.length === 0 ? (
            <p className="text-sm text-muted">
              Nenhuma palavra-chave. A IA continua sinalizando pelo contexto da conversa.
            </p>
          ) : (
            <ul aria-label="Palavras-chave de urgência" className="flex flex-wrap gap-2">
              {keywords.map((keyword) => (
                <li
                  key={keyword.id}
                  className="flex items-center gap-1.5 rounded-full border border-line bg-surface py-1 pl-3 pr-1.5 text-sm text-ink"
                >
                  {keyword.keyword}
                  <button
                    type="button"
                    onClick={() => void handleRemove(keyword)}
                    disabled={busy}
                    aria-label={`Remover ${keyword.keyword}`}
                    className="flex h-5 w-5 items-center justify-center rounded-full text-muted transition-colors hover:bg-danger/10 hover:text-danger disabled:opacity-50"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}

          <div>
            <button
              type="button"
              onClick={() => void handleRestore()}
              disabled={busy}
              className="text-sm font-medium text-accent disabled:opacity-50"
            >
              Restaurar lista padrão
            </button>
            <p className="mt-1 text-xs text-muted">
              Adiciona de volta as palavras padrão que foram removidas, sem apagar as suas.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
