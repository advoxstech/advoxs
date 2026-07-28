"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";

import { MonthlyBarChart } from "./MonthlyBarChart";

type ByMonth = { month: string; total_brl: number };

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

export function SpendingChart() {
  const [preset, setPreset] = useState<Preset>("30");
  const [range, setRange] = useState(() => rangeForPreset("30"));
  const [byMonth, setByMonth] = useState<ByMonth[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoaded(false);
      setError(null);
      try {
        const response = await backendFetch(`billing/spending?from=${range.from}&to=${range.to}`);
        if (!active) return;
        if (!response.ok) {
          setError("Falha ao carregar o gasto — tente novamente.");
          setByMonth([]);
          return;
        }
        const body = await response.json().catch(() => null);
        if (!body || !Array.isArray(body.by_month)) {
          setError("Falha ao carregar o gasto — tente novamente.");
          setByMonth([]);
          return;
        }
        setByMonth(body.by_month);
      } catch {
        if (active) {
          setError("Falha ao carregar o gasto — tente novamente.");
          setByMonth([]);
        }
      } finally {
        if (active) setLoaded(true);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [range]);

  function selectPreset(next: Preset) {
    setPreset(next);
    if (next !== "custom") {
      setRange(rangeForPreset(next));
    }
  }

  return (
    <div>
      <h2 className="font-display text-lg font-semibold text-ink">Gasto com créditos</h2>
      <div className="mt-3 flex flex-wrap items-center gap-3">
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
      {!loaded ? (
        <p className="mt-4 text-sm text-muted">Carregando...</p>
      ) : error ? (
        <p role="alert" className="mt-4 text-sm text-danger">
          {error}
        </p>
      ) : (
        <div className="mt-4 max-w-2xl">
          <MonthlyBarChart data={byMonth} />
        </div>
      )}
    </div>
  );
}
