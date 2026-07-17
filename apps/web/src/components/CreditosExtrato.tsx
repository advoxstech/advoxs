"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import { formatCredits, formatFullDateTime } from "@/lib/format";

type Transaction = {
  id: string;
  type: string;
  amount_credits: number;
  description: string | null;
  created_at: string;
};

const TYPE_LABEL: Record<string, string> = {
  purchase: "Compra",
  consumption: "Consumo",
  resale: "Revenda",
  adjustment: "Ajuste",
  refund: "Reembolso",
  bonus: "Bônus",
};

export function CreditosExtrato() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const response = await backendFetch("billing/transactions");
        if (response.ok) {
          setTransactions(await response.json());
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, []);

  return (
    <div>
      <h2 className="font-display text-lg font-semibold text-ink">Extrato</h2>
      {!loaded ? (
        <p className="mt-3 text-sm text-muted">Carregando...</p>
      ) : (
        <ul className="mt-3 divide-y divide-line rounded-sm border border-line bg-surface">
          {transactions.length === 0 && (
            <li className="px-4 py-3 text-sm text-muted">Nenhuma transação ainda.</li>
          )}
          {transactions.map((t) => (
            <li key={t.id} className="flex items-center justify-between px-4 py-3 text-sm">
              <div>
                <p className="text-ink">{t.description ?? TYPE_LABEL[t.type] ?? t.type}</p>
                <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted">
                  {TYPE_LABEL[t.type] ?? t.type} · {formatFullDateTime(t.created_at)}
                </p>
              </div>
              <span
                className={`font-mono text-sm ${t.amount_credits < 0 ? "text-danger" : "text-accent"}`}
              >
                {t.amount_credits > 0 ? "+" : ""}
                {formatCredits(t.amount_credits)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
