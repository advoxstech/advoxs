"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import { formatBRL, formatPhone } from "@/lib/format";

import { MonthlyBarChart } from "./MonthlyBarChart";

type ByMonth = { month: string; total_brl: number };
type ByCustomer = { contact_phone_number: string; total_brl: number };
type Report = { by_month: ByMonth[]; by_customer: ByCustomer[] };

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

const EMPTY_REPORT: Report = { by_month: [], by_customer: [] };

export function RevenueReport() {
  const [preset, setPreset] = useState<Preset>("30");
  const [range, setRange] = useState(() => rangeForPreset("30"));
  const [report, setReport] = useState<Report>(EMPTY_REPORT);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoaded(false);
      setError(null);
      try {
        const response = await backendFetch(
          `end-customer-billing/revenue?from=${range.from}&to=${range.to}`,
        );
        if (!active) return;
        if (!response.ok) {
          setError("Falha ao carregar o relatório — tente novamente.");
          setReport(EMPTY_REPORT);
          return;
        }
        const body = await response.json().catch(() => null);
        if (!body || !Array.isArray(body.by_month) || !Array.isArray(body.by_customer)) {
          setError("Falha ao carregar o relatório — tente novamente.");
          setReport(EMPTY_REPORT);
          return;
        }
        setReport(body);
      } catch {
        if (active) {
          setError("Falha ao carregar o relatório — tente novamente.");
          setReport(EMPTY_REPORT);
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

      {!loaded ? (
        <p className="mt-6 text-sm text-muted">Carregando...</p>
      ) : error ? (
        <p role="alert" className="mt-6 text-sm text-danger">
          {error}
        </p>
      ) : (
        <>
          <div className="mt-6 max-w-2xl">
            <MonthlyBarChart data={report.by_month} />
          </div>

          <h3 className="mt-8 font-display text-sm font-semibold text-ink">Por cliente</h3>
          <table className="mt-3 w-full max-w-xl text-left text-sm">
            <thead>
              <tr className="border-b border-line text-xs uppercase tracking-[0.1em] text-muted">
                <th className="py-2">Contato</th>
                <th className="py-2">Faturado no período</th>
              </tr>
            </thead>
            <tbody>
              {report.by_customer.length === 0 ? (
                <tr>
                  <td className="py-4 text-sm text-muted" colSpan={2}>
                    Nenhum cliente comprou no período selecionado.
                  </td>
                </tr>
              ) : (
                report.by_customer.map((row) => (
                  <tr key={row.contact_phone_number} className="border-b border-line">
                    <td className="py-3">{formatPhone(row.contact_phone_number)}</td>
                    <td className="py-3 font-mono">R$ {formatBRL(row.total_brl)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
