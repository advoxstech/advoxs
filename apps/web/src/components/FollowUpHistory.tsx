"use client";

import { useCallback, useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type { FollowUpBatch, FollowUpBatchDetail } from "@/lib/types";

const textAction =
  "text-action text-muted transition-colors hover:text-ink disabled:opacity-40";

const RECIPIENT_STATUS: Record<string, string> = {
  pending: "Na fila",
  processing: "Enviando",
  sent: "Enviado",
  failed: "Falhou",
  skipped: "Pulado",
};

function summary(batch: FollowUpBatch) {
  const parts = [`${batch.sent} de ${batch.total} enviados`];
  if (batch.failed) parts.push(`${batch.failed} com falha`);
  if (batch.skipped) parts.push(`${batch.skipped} pulados`);
  if (batch.pending) parts.push(`${batch.pending} na fila`);
  return parts.join(" · ");
}

/** Envios de follow-up do escritório, com a situação de cada contato. */
export function FollowUpHistory({
  refreshKey = 0,
  pollMs = 5000,
}: {
  refreshKey?: number;
  pollMs?: number;
}) {
  const [batches, setBatches] = useState<FollowUpBatch[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<FollowUpBatchDetail | null>(null);

  const load = useCallback(async () => {
    try {
      const response = await backendFetch("follow-ups?limit=20");
      if (!response.ok) throw new Error("follow-ups request failed");
      setBatches(await response.json());
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, []);

  const loadDetail = useCallback(async (id: string) => {
    const response = await backendFetch(`follow-ups/${id}`);
    if (response.ok) setDetail(await response.json());
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const hasPending = batches.some((batch) => batch.pending > 0);
  useEffect(() => {
    if (!hasPending || !pollMs) return;
    const timer = window.setInterval(() => {
      void load();
      if (openId) void loadDetail(openId);
    }, pollMs);
    return () => window.clearInterval(timer);
  }, [hasPending, load, loadDetail, openId, pollMs]);

  const toggle = (id: string) => {
    if (openId === id) {
      setOpenId(null);
      setDetail(null);
      return;
    }
    setOpenId(id);
    setDetail(null);
    void loadDetail(id);
  };

  return (
    <section aria-label="Envios de follow-up" className="mt-8">
      <h3 className="mb-2 font-medium">Envios de follow-up</h3>
      {loadError && (
        <p role="alert" className="text-sm text-danger">
          Não foi possível carregar o histórico de envios.
        </p>
      )}
      {!loadError && !batches.length && (
        <p className="text-sm text-muted">Nenhum follow-up enviado ainda.</p>
      )}
      <ul className="divide-y divide-line">
        {batches.map((batch) => (
          <li key={batch.id} className="py-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">
                  {batch.template_name ?? batch.text ?? "Follow-up"}
                </p>
                <p className="text-xs text-muted">
                  {new Date(batch.created_at).toLocaleString("pt-BR", {
                    dateStyle: "short",
                    timeStyle: "short",
                  })}{" "}
                  · {batch.created_by_name || "Equipe"} · {summary(batch)}
                </p>
              </div>
              <button
                type="button"
                className={textAction}
                aria-expanded={openId === batch.id}
                onClick={() => toggle(batch.id)}
              >
                {openId === batch.id ? "Ocultar contatos" : "Ver contatos"}
              </button>
            </div>
            {openId === batch.id && (
              <ul className="mt-2 space-y-1 text-sm">
                {!detail && <li className="text-muted">Carregando…</li>}
                {detail?.recipients.map((recipient) => (
                  <li
                    key={recipient.id}
                    className="flex flex-wrap justify-between gap-2"
                  >
                    <span>
                      {recipient.contact_name
                        ? `${recipient.contact_name} · `
                        : ""}
                      +{recipient.phone}
                    </span>
                    <span
                      className={
                        recipient.status === "failed"
                          ? "text-danger"
                          : "text-muted"
                      }
                    >
                      {RECIPIENT_STATUS[recipient.status] ?? recipient.status}
                      {recipient.error ? ` — ${recipient.error}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
