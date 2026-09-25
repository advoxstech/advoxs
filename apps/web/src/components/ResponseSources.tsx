"use client";

import { useState } from "react";

import type { Message, ResponseSource } from "@/lib/types";

const STATUS_TEXT = {
  referenced: "Referências indicadas pelo agente e verificadas na consulta à base.",
  no_documents: "Resposta sem consulta à base do escritório: o agente não tem documentos vinculados.",
  not_searched: "Base não consultada nesta resposta.",
  empty: "Nenhum conteúdo encontrado na consulta.",
  not_used: "Resposta sem referências da base. A consulta retornou trechos, mas nenhum foi indicado como fonte.",
  unavailable: "Base temporariamente indisponível durante esta resposta.",
};

function OriginalDocument({ source }: { source: ResponseSource }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/backend/knowledge-base/files/${encodeURIComponent(source.document_id)}/content`,
        { cache: "no-store" },
      );
      if (!response.ok) {
        setError(response.status === 404
          ? "O documento original não está mais disponível. O trecho utilizado foi preservado."
          : "Não foi possível abrir o documento. Tente novamente.");
        return;
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = source.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setError("Não foi possível abrir o documento. Verifique sua conexão e tente novamente.");
    } finally {
      setLoading(false);
    }
  }

  return <>
    <button type="button" onClick={() => void download()} disabled={loading}
      className="mt-2 min-h-9 rounded-sm px-2 text-xs font-medium text-accent underline underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50">
      {loading ? "Abrindo…" : "Baixar documento original"}
    </button>
    {error ? <p role="alert" className="mt-1 text-xs text-danger">{error}</p> : null}
  </>;
}

export function ResponseSources({ message }: { message: Message }) {
  if (message.sender_type !== "agent" || message.media_url) return null;
  const evidence = message.response_sources;
  const count = new Set(evidence?.sources.map((source) => source.document_id)).size;
  return (
    <details className="mt-3 min-w-0 border-t border-line pt-1 text-xs">
      <summary className="min-h-9 cursor-pointer rounded-sm py-2 text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
        Fontes da resposta{count ? ` · ${count} ${count === 1 ? "documento" : "documentos"}` : ""}
      </summary>
      <div className="space-y-3 pb-1">
        <p className="text-muted">{evidence
          ? STATUS_TEXT[evidence.status]
          : "Fontes não registradas para esta mensagem."}</p>
        {evidence?.search_failed && evidence.status !== "unavailable" ? (
          <p className="text-muted">Uma das consultas à base falhou durante esta resposta.</p>
        ) : null}
        {evidence?.sources.map((source) => (
          <article key={`${source.document_id}:${source.chunk_id}`} className="min-w-0 rounded-sm border border-line bg-surface p-3">
            <p className="break-words font-medium text-ink">{source.filename}</p>
            {source.page ? <p className="mt-1 text-muted">Página {source.page}</p> : null}
            <blockquote className="mt-2 max-h-56 overflow-y-auto whitespace-pre-wrap break-words border-l-2 border-line pl-3 leading-relaxed text-ink">
              {source.excerpt}
            </blockquote>
            <OriginalDocument source={source} />
          </article>
        ))}
        <p className="text-muted">Visível somente para o escritório. As fontes ajudam na conferência e não garantem a interpretação da resposta.</p>
      </div>
    </details>
  );
}
