"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";

type Payout = {
  amount_brl: number;
  status: string;
  arrival_date: string | null;
};

type Earnings = {
  available_brl: number;
  pending_brl: number;
  recent_payouts: Payout[];
};

const STATUS_LABEL: Record<string, string> = {
  paid: "pago",
  pending: "pendente",
  in_transit: "a caminho",
  canceled: "cancelado",
  failed: "falhou",
};

function formatBRL(value: number): string {
  return value.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function ConnectEarnings() {
  const [earnings, setEarnings] = useState<Earnings | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const response = await backendFetch("end-customer-billing/connect-account/earnings");
        if (!active) return;
        if (response.status === 404) {
          // Conta ainda não configurada — não é erro, só não mostra nada.
          setLoaded(true);
          return;
        }
        if (!response.ok) {
          setError("Falha ao carregar o saldo — tente novamente.");
          setLoaded(true);
          return;
        }
        setEarnings(await response.json());
      } catch {
        if (active) setError("Falha de conexão — tente novamente.");
      } finally {
        if (active) setLoaded(true);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, []);

  if (!loaded || (!earnings && !error)) return null;

  return (
    <section className="mt-6 max-w-xl">
      <h3 className="font-display text-sm font-semibold text-ink">Quanto você já recebeu</h3>
      {error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {error}
        </p>
      )}
      {earnings && (
        <>
          <div className="mt-3 flex gap-6">
            <div>
              <p className="text-xs uppercase tracking-[0.1em] text-muted">Disponível</p>
              <p className="font-mono text-lg text-ink">R$ {formatBRL(earnings.available_brl)}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-[0.1em] text-muted">Pendente</p>
              <p className="font-mono text-lg text-ink">R$ {formatBRL(earnings.pending_brl)}</p>
            </div>
          </div>
          {earnings.recent_payouts.length > 0 && (
            <ul className="mt-4">
              {earnings.recent_payouts.map((payout, index) => (
                <li
                  key={index}
                  className="flex items-center justify-between border-b border-line py-2 text-sm"
                >
                  <span className="text-muted">
                    {payout.arrival_date
                      ? new Date(payout.arrival_date).toLocaleDateString("pt-BR")
                      : "Data a definir"}
                  </span>
                  <span className="font-mono text-ink">R$ {formatBRL(payout.amount_brl)}</span>
                  <span className="text-xs uppercase tracking-[0.1em] text-muted">
                    {STATUS_LABEL[payout.status] ?? payout.status}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
