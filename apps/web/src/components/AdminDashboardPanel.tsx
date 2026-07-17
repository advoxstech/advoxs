"use client";

import { useEffect, useState } from "react";

import { adminBackendFetch } from "@/lib/admin-client-api";
import { formatCredits } from "@/lib/format";
import type { AdminDashboard } from "@/lib/types";

import { NewTenantsChart } from "./NewTenantsChart";
import { StatTile } from "./StatTile";

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

export function AdminDashboardPanel() {
  const [data, setData] = useState<AdminDashboard | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const response = await adminBackendFetch("platform-admin/dashboard");
        if (response.ok) {
          setData(await response.json());
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, []);

  if (!loaded) {
    return <p className="p-8 text-sm text-muted">Carregando...</p>;
  }
  if (!data) {
    return <p className="p-8 text-sm text-danger">Não foi possível carregar o dashboard.</p>;
  }

  return (
    <div className="flex flex-col gap-8 p-8">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatTile label="Escritórios" value={String(data.tenants_total)} />
        <StatTile label="Ativos" value={String(data.tenants_by_status.active)} tone="good" />
        <StatTile label="Suspensos" value={String(data.tenants_by_status.suspended)} tone="critical" />
        <StatTile
          label="WhatsApp conectado"
          value={`${data.whatsapp_connected.connected} / ${data.whatsapp_connected.total}`}
        />
        <StatTile
          label="Receita (30 dias)"
          value={`R$ ${Number(data.revenue_brl_last_30_days).toFixed(2)}`}
        />
        <StatTile label="Créditos vendidos" value={formatCredits(data.credits_summary.sold)} />
        <StatTile
          label="Créditos consumidos"
          value={formatCredits(data.credits_summary.consumed)}
        />
        <StatTile label="Mensagens processadas" value={String(data.messages_processed)} />
        <StatTile label="Execuções de agente" value={String(data.agent_executions)} />
        <StatTile label="Tokens consumidos" value={String(data.tokens_consumed)} />
        <StatTile label="Arquivos de KB" value={String(data.knowledge_base_usage.total_files)} />
        <StatTile
          label="Storage de KB"
          value={formatBytes(data.knowledge_base_usage.total_size_bytes)}
        />
      </div>

      <div>
        <h2 className="font-display text-lg font-semibold text-ink">
          Novos escritórios (30 dias)
        </h2>
        <div className="mt-3 rounded-sm border border-line bg-surface p-4">
          <NewTenantsChart data={data.new_tenants_last_30_days} />
        </div>
      </div>

      <div>
        <h2 className="font-display text-lg font-semibold text-ink">Menor saldo de créditos</h2>
        <ul className="mt-3 divide-y divide-line rounded-sm border border-line bg-surface">
          {data.low_balance_tenants.map((t) => (
            <li key={t.id} className="flex items-center justify-between px-4 py-3 text-sm">
              <a
                href={`/admin/tenants/${t.id}`}
                className="text-ink hover:text-accent hover:underline"
              >
                {t.name}
              </a>
              <span className="font-mono text-muted">{formatCredits(t.credit_balance)} créditos</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
