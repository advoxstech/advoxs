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

export function UrgencyKeywordsPanel() {
  const [keywords, setKeywords] = useState<UrgencyKeyword[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const response = await backendFetch("urgency-keywords");
        if (response.ok) setKeywords(await response.json());
        else setFeedback("Não foi possível carregar as palavras-chave.");
      } catch {
        setFeedback("Falha de conexão ao carregar as palavras-chave.");
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, []);

  async function handleAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const keyword = draft.trim();
    if (!keyword || busy) return;
    setBusy(true);
    setFeedback(null);
    try {
      const response = await backendFetch("urgency-keywords", {
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
      const response = await backendFetch(`urgency-keywords/${keyword.id}`, { method: "DELETE" });
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
      const response = await backendFetch("urgency-keywords/restore-defaults", {
        method: "POST",
      });
      if (!response.ok) {
        setFeedback("Não foi possível restaurar a lista padrão.");
        return;
      }
      setKeywords(await response.json());
    } catch {
      setFeedback("Falha de conexão. Tente novamente.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      <header className="border-b border-line px-8 py-5">
        <h1 className="font-display text-[26px] leading-tight font-semibold text-ink">Urgência</h1>
        <p className="text-sm text-muted">
          Conversas urgentes ficam destacadas em vermelho em Conversas. A IA sinaliza pelo
          contexto da conversa, e qualquer mensagem do cliente que contenha uma das palavras abaixo
          também é sinalizada, mesmo com a IA pausada ou em atendimento humano.
        </p>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <div className="flex max-w-2xl flex-col gap-5">
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
    </main>
  );
}
