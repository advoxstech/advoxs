"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";

import { ConversationsUsageReport } from "./ConversationsUsageReport";
import { EndCustomerBillingPanel } from "./EndCustomerBillingPanel";
import { EndCustomerList } from "./EndCustomerList";
import { RevenueReport } from "./RevenueReport";

type Tab = "config" | "clientes" | "faturamento" | "consumo";

export function EndCustomerBillingTabs() {
  const [tab, setTab] = useState<Tab>("config");
  const [enabled, setEnabled] = useState(false);
  const [billingProvider, setBillingProvider] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const response = await backendFetch("end-customer-billing/settings");
        if (response.ok) {
          const body = await response.json();
          setEnabled(Boolean(body.enabled));
          setBillingProvider(body.billing_provider);
        }
      } catch {
        // fail-safe: sem settings carregadas, as abas Clientes/Faturamento ficam escondidas
      }
    }
    void load();
  }, []);

  const showRevenueReport = enabled && billingProvider === "connect";

  const tabClass = (active: boolean) =>
    `rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
      active ? "bg-ink text-ground" : "text-muted hover:text-ink"
    }`;

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex gap-1 border-b border-line px-8 py-3">
        <button
          type="button"
          onClick={() => setTab("config")}
          aria-pressed={tab === "config"}
          className={tabClass(tab === "config")}
        >
          Configurações
        </button>
        {enabled && (
          <button
            type="button"
            onClick={() => setTab("clientes")}
            aria-pressed={tab === "clientes"}
            className={tabClass(tab === "clientes")}
          >
            Clientes
          </button>
        )}
        {showRevenueReport && (
          <button
            type="button"
            onClick={() => setTab("faturamento")}
            aria-pressed={tab === "faturamento"}
            className={tabClass(tab === "faturamento")}
          >
            Faturamento
          </button>
        )}
        <button
          type="button"
          onClick={() => setTab("consumo")}
          aria-pressed={tab === "consumo"}
          className={tabClass(tab === "consumo")}
        >
          Consumo
        </button>
      </div>

      {tab === "config" && <EndCustomerBillingPanel />}
      {tab === "clientes" && enabled && <EndCustomerList />}
      {tab === "faturamento" && showRevenueReport && <RevenueReport />}
      {tab === "consumo" && <ConversationsUsageReport />}
    </div>
  );
}
