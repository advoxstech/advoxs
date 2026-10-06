"use client";

import { useState } from "react";

import { backendFetch } from "@/lib/client-api";
import { formatFullDateTime } from "@/lib/format";
import type { Conversation } from "@/lib/types";

const SOURCE_LABEL: Record<NonNullable<Conversation["urgent_source"]>, string> = {
  agent: "Sinalizada pela IA",
  keyword: "Palavra-chave do escritório",
  manual: "Marcada pela equipe",
};

export function useUrgencyUpdate(
  conversation: Conversation,
  onUpdate?: (conversation: Conversation) => void,
) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const update = async (urgent: boolean) => {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      const response = await backendFetch(`conversations/${conversation.id}/urgency`, {
        method: "PATCH",
        body: JSON.stringify({ urgent }),
      });
      if (!response.ok) {
        setError("Não foi possível atualizar a urgência. Tente novamente.");
        return;
      }
      onUpdate?.(await response.json());
    } catch {
      setError("Falha de conexão. Tente novamente.");
    } finally {
      setSaving(false);
    }
  };

  return { saving, error, update };
}

export function UrgencyBanner({
  conversation,
  onUpdate,
}: {
  conversation: Conversation;
  onUpdate?: (conversation: Conversation) => void;
}) {
  const { saving, error, update } = useUrgencyUpdate(conversation, onUpdate);
  if (!conversation.urgent_since) return null;

  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 border-b border-danger/30 bg-danger/10 px-4 py-2.5 text-sm md:px-6"
    >
      <div className="min-w-0">
        <p className="font-semibold text-danger">Atendimento urgente</p>
        <p className="text-xs text-ink">
          {conversation.urgent_reason}
          <span className="text-muted">
            {" · "}
            {conversation.urgent_source ? SOURCE_LABEL[conversation.urgent_source] : null}
            {" · desde "}
            {formatFullDateTime(conversation.urgent_since)}
          </span>
        </p>
        {error ? <p className="mt-1 text-xs text-danger">{error}</p> : null}
      </div>
      <button
        type="button"
        onClick={() => void update(false)}
        disabled={saving}
        className="shrink-0 rounded-sm border border-danger/40 bg-surface px-3 py-1.5 text-xs font-medium text-danger transition-colors hover:bg-danger hover:text-white disabled:opacity-50"
      >
        {saving ? "Salvando…" : "Marcar como resolvida"}
      </button>
    </div>
  );
}

export function UrgentTag() {
  return (
    <span className="inline-flex items-center gap-1 rounded-sm bg-danger px-1.5 py-0.5 font-mono text-micro font-semibold uppercase tracking-[0.12em] text-white">
      Urgente
    </span>
  );
}
