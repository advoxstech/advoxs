"use client";

import { useEffect, useState } from "react";

import { adminBackendFetch } from "@/lib/admin-client-api";
import { formatCredits } from "@/lib/format";

type TenantListItem = {
  id: string;
  name: string;
  status: "active" | "suspended";
  credit_balance: number;
  created_at: string;
  whatsapp_connected: boolean;
};

const STATUS_LABEL: Record<TenantListItem["status"], string> = {
  active: "ativo",
  suspended: "suspenso",
};

const STATUS_CLASS: Record<TenantListItem["status"], string> = {
  active: "bg-accent-soft text-accent",
  suspended: "bg-danger/10 text-danger",
};

export function AdminTenantsList() {
  const [tenants, setTenants] = useState<TenantListItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const response = await adminBackendFetch("platform-admin/tenants");
        if (response.ok) {
          setTenants(await response.json());
        } else {
          setError(true);
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
  if (error) {
    return (
      <p className="p-8 text-sm text-danger">Não foi possível carregar os escritórios.</p>
    );
  }

  return (
    <div className="p-8">
      <h1 className="font-display text-xl font-semibold text-ink">Escritórios</h1>
      <table className="mt-6 w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line text-xs uppercase tracking-[0.1em] text-muted">
            <th className="py-2">Nome</th>
            <th className="py-2">Status</th>
            <th className="py-2">Créditos</th>
            <th className="py-2">WhatsApp</th>
            <th className="py-2">Criado em</th>
          </tr>
        </thead>
        <tbody>
          {tenants.map((t) => (
            <tr key={t.id} className="border-b border-line">
              <td className="py-3">
                <a
                  href={`/admin/tenants/${t.id}`}
                  className="text-ink hover:text-accent hover:underline"
                >
                  {t.name}
                </a>
              </td>
              <td className="py-3">
                <span
                  className={`rounded-full px-3 py-1 font-mono text-[10px] uppercase tracking-[0.15em] ${STATUS_CLASS[t.status]}`}
                >
                  {STATUS_LABEL[t.status]}
                </span>
              </td>
              <td className="py-3 font-mono text-muted">{formatCredits(t.credit_balance)}</td>
              <td className="py-3 text-muted">{t.whatsapp_connected ? "Sim" : "Não"}</td>
              <td className="py-3 text-muted">
                {new Date(t.created_at).toLocaleDateString("pt-BR")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
