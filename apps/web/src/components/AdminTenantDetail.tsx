"use client";

import { useEffect, useState } from "react";

import { adminBackendFetch } from "@/lib/admin-client-api";

type TenantDetail = {
  id: string;
  name: string;
  email_contato: string;
  status: "active" | "suspended";
  credit_balance: number;
  created_at: string;
  recent_transactions: {
    id: string;
    type: string;
    amount_credits: number;
    description: string | null;
    created_at: string;
  }[];
  knowledge_base_files: {
    id: string;
    filename: string;
    status: string;
    uploaded_at: string;
  }[];
};

export function AdminTenantDetail({ tenantId }: { tenantId: string }) {
  const [tenant, setTenant] = useState<TenantDetail | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const response = await adminBackendFetch(`platform-admin/tenants/${tenantId}`);
        if (response.status === 404) {
          setNotFound(true);
        } else if (response.ok) {
          setTenant(await response.json());
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, [tenantId]);

  if (!loaded) {
    return <p className="p-8 text-sm text-muted">Carregando...</p>;
  }
  if (notFound || !tenant) {
    return <p className="p-8 text-sm text-danger">Escritório não encontrado.</p>;
  }

  return (
    <div className="flex flex-col gap-8 p-8">
      <div>
        <h1 className="font-display text-2xl font-semibold text-ink">{tenant.name}</h1>
        <p className="mt-1 text-sm text-muted">
          {tenant.email_contato} · {tenant.credit_balance} créditos · criado em{" "}
          {new Date(tenant.created_at).toLocaleDateString("pt-BR")}
        </p>
      </div>

      <div>
        <h2 className="font-display text-lg font-semibold text-ink">Transações recentes</h2>
        <ul className="mt-3 divide-y divide-line rounded-sm border border-line bg-surface">
          {tenant.recent_transactions.map((t) => (
            <li key={t.id} className="flex items-center justify-between px-4 py-3 text-sm">
              <span className="text-ink">{t.description ?? t.type}</span>
              <span className="font-mono text-muted">{t.amount_credits}</span>
            </li>
          ))}
          {tenant.recent_transactions.length === 0 && (
            <li className="px-4 py-3 text-sm text-muted">Sem transações ainda.</li>
          )}
        </ul>
      </div>

      <div>
        <h2 className="font-display text-lg font-semibold text-ink">Base de conhecimento</h2>
        <ul className="mt-3 divide-y divide-line rounded-sm border border-line bg-surface">
          {tenant.knowledge_base_files.map((f) => (
            <li key={f.id} className="flex items-center justify-between px-4 py-3 text-sm">
              <span className="text-ink">{f.filename}</span>
              <span className="text-muted">{f.status}</span>
            </li>
          ))}
          {tenant.knowledge_base_files.length === 0 && (
            <li className="px-4 py-3 text-sm text-muted">Nenhum arquivo enviado.</li>
          )}
        </ul>
      </div>
    </div>
  );
}
