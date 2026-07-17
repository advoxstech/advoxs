"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type { TenantDashboard } from "@/lib/types";

import { StatTile } from "./StatTile";

const STATE_LABEL: Record<"agent" | "human", string> = {
  agent: "agente",
  human: "humano",
};

export function DashboardPanel() {
  const [data, setData] = useState<TenantDashboard | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const response = await backendFetch("dashboard");
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
    return <p className="p-8 text-sm text-danger">Não foi possível carregar o painel.</p>;
  }

  return (
    <div className="flex flex-col gap-8 p-8">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
        <Link href="/creditos">
          <StatTile
            label="Saldo de créditos"
            value={String(data.credit_balance)}
            tone={data.credit_balance <= 0 ? "critical" : "neutral"}
          />
        </Link>
        <Link href="/configuracoes/whatsapp">
          <StatTile
            label="WhatsApp"
            value={
              data.whatsapp.connected
                ? (data.whatsapp.display_phone_number ?? "Conectado")
                : "Desconectado"
            }
            tone={data.whatsapp.connected ? "good" : "critical"}
          />
        </Link>
        <Link href="/conversas">
          <StatTile label="Conversas" value={String(data.conversations.total)} />
        </Link>
        <Link href="/conversas">
          <StatTile
            label="Aguardando você"
            value={String(data.conversations.waiting_human)}
            tone={data.conversations.waiting_human > 0 ? "warning" : "neutral"}
          />
        </Link>
        <StatTile
          label="Respostas do agente (30 dias)"
          value={String(data.usage_last_30_days.agent_messages)}
        />
        <StatTile
          label="Créditos consumidos (30 dias)"
          value={String(data.usage_last_30_days.credits_consumed)}
        />
        <Link href="/base-de-conhecimento">
          <StatTile label="Arquivos na base" value={String(data.knowledge_base.ready)} />
        </Link>
        {data.knowledge_base.error > 0 && (
          <Link href="/base-de-conhecimento">
            <StatTile
              label="Arquivos com erro"
              value={String(data.knowledge_base.error)}
              tone="critical"
            />
          </Link>
        )}
      </div>

      <div>
        <h2 className="font-display text-lg font-semibold text-ink">Conversas recentes</h2>
        <ul className="mt-3 divide-y divide-line rounded-none border border-line bg-surface">
          {data.recent_conversations.map((c) => (
            <li key={c.id}>
              <Link
                href="/conversas"
                className="flex items-center justify-between px-4 py-3 text-sm hover:bg-ground"
              >
                <span className="text-ink">{c.contact_phone_number}</span>
                <span className="flex items-center gap-4">
                  <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted">
                    {STATE_LABEL[c.state]}
                  </span>
                  <span className="text-muted">
                    {c.last_message_at
                      ? new Date(c.last_message_at).toLocaleString("pt-BR")
                      : "—"}
                  </span>
                </span>
              </Link>
            </li>
          ))}
          {data.recent_conversations.length === 0 && (
            <li className="px-4 py-3 text-sm text-muted">Nenhuma conversa ainda.</li>
          )}
        </ul>
      </div>
    </div>
  );
}
