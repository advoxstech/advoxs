"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";

type UsageSummary = {
  executions: number;
  operational_credits: number;
  billed_credits: number;
  shortfall_credits: number;
  subscription_credits: number;
  tenant_credits: number;
  end_customer_credits: number;
  document_credits: number;
  tokens_input: number;
  tokens_output: number;
};

type UsageItem = {
  id: string;
  contact_phone_number: string;
  funding_source: "tenant" | "end_customer_credits" | "end_customer_subscription";
  operational_credits: number;
  billed_credits: number;
  shortfall_credits: number;
  created_at: string;
};

type UsageReport = { summary: UsageSummary; items: UsageItem[] };

const sourceLabels: Record<UsageItem["funding_source"], string> = {
  tenant: "Créditos do escritório",
  end_customer_credits: "Créditos do cliente",
  end_customer_subscription: "Assinatura do cliente",
};

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function formatCredits(value: number): string {
  return value.toLocaleString("pt-BR", { maximumFractionDigits: 4 });
}

function maskPhone(phone: string): string {
  return phone.length > 4 ? `•••• ${phone.slice(-4)}` : phone;
}

export function FinancialUsagePanel() {
  const [report, setReport] = useState<UsageReport | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    const to = new Date();
    const from = new Date();
    from.setDate(from.getDate() - 30);

    async function load() {
      try {
        const response = await backendFetch(
          `billing/usage?from=${isoDate(from)}&to=${isoDate(to)}&limit=10`,
        );
        const body = response.ok ? await response.json().catch(() => null) : null;
        if (!active) return;
        if (!body?.summary || !Array.isArray(body.items)) {
          setError(true);
          return;
        }
        setReport(body);
      } catch {
        if (active) setError(true);
      }
    }

    void load();
    return () => {
      active = false;
    };
  }, []);

  return (
    <section className="mt-8" aria-labelledby="financial-usage-title">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h2 id="financial-usage-title" className="font-display text-lg font-semibold text-ink">
            Consumo financeiro
          </h2>
          <p className="mt-1 text-sm text-muted">Últimos 30 dias</p>
        </div>
      </div>

      {error ? (
        <p role="alert" className="mt-4 text-sm text-danger">
          Não foi possível carregar o consumo financeiro.
        </p>
      ) : !report ? (
        <p className="mt-4 text-sm text-muted">Carregando...</p>
      ) : (
        <>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Metric label="Custo operacional real" value={formatCredits(report.summary.operational_credits)} />
            <Metric label="Créditos debitados" value={formatCredits(report.summary.billed_credits)} />
            <Metric label="Uso pago pelo escritório" value={formatCredits(report.summary.tenant_credits)} />
            <Metric label="Uso pago por créditos dos clientes" value={formatCredits(report.summary.end_customer_credits)} />
            <Metric
              label="Uso coberto por assinatura de cliente"
              value={formatCredits(report.summary.subscription_credits)}
            />
            <Metric
              label="Custo sem saldo para cobrir"
              value={formatCredits(report.summary.shortfall_credits)}
              warning={report.summary.shortfall_credits > 0}
            />
          </div>

          <div className="mt-4 overflow-hidden rounded border border-line bg-surface">
            <div className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-line px-4 py-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
              <span>Quem custeou</span>
              <span>Custo real</span>
              <span>Créditos debitados</span>
            </div>
            {report.items.length === 0 ? (
              <p className="px-4 py-5 text-sm text-muted">Nenhuma execução no período.</p>
            ) : (
              report.items.map((item) => (
                <div
                  key={item.id}
                  className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-line px-4 py-3 text-sm last:border-b-0"
                >
                  <div>
                    <p className="font-medium text-ink">{sourceLabels[item.funding_source]}</p>
                    <p className="mt-0.5 text-xs text-muted">{maskPhone(item.contact_phone_number)}</p>
                  </div>
                  <span className="self-center text-ink">
                    {formatCredits(item.operational_credits)}
                  </span>
                  <span className="self-center text-ink">{formatCredits(item.billed_credits)}</span>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </section>
  );
}

function Metric({ label, value, warning = false }: { label: string; value: string; warning?: boolean }) {
  return (
    <div className="rounded border border-line bg-surface p-4">
      <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">{label}</p>
      <p className={`mt-2 text-xl font-semibold ${warning ? "text-danger" : "text-ink"}`}>
        {value}
      </p>
      <p className="mt-0.5 text-xs text-muted">créditos</p>
    </div>
  );
}
