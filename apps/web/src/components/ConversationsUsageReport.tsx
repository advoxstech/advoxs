"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import { formatCredits, formatFullDateTime, formatPhone } from "@/lib/format";

type UsageRow = {
  conversation_id: string;
  contact_phone_number: string;
  is_test: boolean;
  credits_consumed: number;
  billed_responses: number;
  last_message_at: string;
};

type Preset = "7" | "30" | "90" | "custom";

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function rangeForPreset(preset: Preset): { from: string; to: string } {
  const to = new Date();
  const from = new Date();
  const days = preset === "custom" ? 30 : Number(preset);
  from.setDate(from.getDate() - days);
  return { from: isoDate(from), to: isoDate(to) };
}

export function ConversationsUsageReport() {
  const [preset, setPreset] = useState<Preset>("30");
  const [range, setRange] = useState(() => rangeForPreset("30"));
  const [rows, setRows] = useState<UsageRow[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    async function load() {
      setLoaded(false);
      try {
        const response = await backendFetch(
          `conversations/usage?from=${range.from}&to=${range.to}`,
        );
        if (response.ok) {
          setRows(await response.json());
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, [range]);

  function selectPreset(next: Preset) {
    setPreset(next);
    if (next !== "custom") {
      setRange(rangeForPreset(next));
    }
  }

  return (
    <div className="flex flex-1 flex-col overflow-y-auto px-8 py-6">
      <div className="flex flex-wrap items-center gap-3">
        {(["7", "30", "90"] as Preset[]).map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => selectPreset(p)}
            aria-pressed={preset === p}
            className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
              preset === p ? "bg-ink text-ground" : "text-muted hover:text-ink"
            }`}
          >
            {p} dias
          </button>
        ))}
        <button
          type="button"
          onClick={() => setPreset("custom")}
          aria-pressed={preset === "custom"}
          className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
            preset === "custom" ? "bg-ink text-ground" : "text-muted hover:text-ink"
          }`}
        >
          Personalizado
        </button>
        {preset === "custom" && (
          <div className="flex items-center gap-2 text-sm text-ink">
            <input
              type="date"
              value={range.from}
              onChange={(event) => setRange((prev) => ({ ...prev, from: event.target.value }))}
              className="rounded border border-line bg-surface px-2 py-1 text-sm"
            />
            <span className="text-muted">até</span>
            <input
              type="date"
              value={range.to}
              onChange={(event) => setRange((prev) => ({ ...prev, to: event.target.value }))}
              className="rounded border border-line bg-surface px-2 py-1 text-sm"
            />
          </div>
        )}
      </div>

      <table className="mt-6 w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line text-xs uppercase tracking-[0.1em] text-muted">
            <th className="py-2">Contato</th>
            <th className="py-2">Créditos consumidos</th>
            <th className="py-2">Respostas do agente</th>
            <th className="py-2">Última atividade</th>
          </tr>
        </thead>
        <tbody>
          {!loaded ? (
            <tr>
              <td className="py-4 text-sm text-muted" colSpan={4}>
                Carregando...
              </td>
            </tr>
          ) : rows.length === 0 ? (
            <tr>
              <td className="py-4 text-sm text-muted" colSpan={4}>
                Nenhum consumo no período selecionado.
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr key={row.conversation_id} className="border-b border-line">
                <td className="py-3">
                  {formatPhone(row.contact_phone_number)}
                  {row.is_test && (
                    <span className="ml-2 rounded-sm bg-brass-soft px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-brass">
                      teste
                    </span>
                  )}
                </td>
                <td className="py-3 font-mono">{formatCredits(row.credits_consumed)}</td>
                <td className="py-3">{row.billed_responses}</td>
                <td className="py-3 text-muted">{formatFullDateTime(row.last_message_at)}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
