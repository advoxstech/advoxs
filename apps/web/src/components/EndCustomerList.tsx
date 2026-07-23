"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import { formatCredits, formatPhone } from "@/lib/format";

type Customer = {
  contact_phone_number: string;
  credit_balance: number;
  total_purchased: number;
  total_consumed: number;
};

export function EndCustomerList() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const response = await backendFetch("end-customer-billing/customers");
        if (response.ok) {
          setCustomers(await response.json());
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, []);

  async function handleRemoveCredits(contactPhoneNumber: string) {
    if (
      !window.confirm(
        `Remover os créditos de ${formatPhone(contactPhoneNumber)}? O saldo será zerado e, se o cliente entrar em contato de novo, vai precisar comprar créditos novamente.`,
      )
    ) {
      return;
    }
    setFeedback(null);
    setRemoving(contactPhoneNumber);
    try {
      const response = await backendFetch(
        `end-customer-billing/customers/${encodeURIComponent(contactPhoneNumber)}/zero-balance`,
        { method: "POST" },
      );
      if (!response.ok) {
        setFeedback("Falha ao remover os créditos — tente novamente.");
        return;
      }
      setCustomers(
        customers.map((c) =>
          c.contact_phone_number === contactPhoneNumber ? { ...c, credit_balance: 0 } : c,
        ),
      );
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setRemoving(null);
    }
  }

  return (
    <div className="mt-8">
      <h2 className="font-display text-lg font-semibold text-ink">Clientes finais</h2>
      {feedback && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {feedback}
        </p>
      )}
      {!loaded ? (
        <p className="mt-3 text-sm text-muted">Carregando...</p>
      ) : customers.length === 0 ? (
        <p className="mt-3 text-sm text-muted">Nenhum cliente comprou créditos ainda.</p>
      ) : (
        <table className="mt-4 w-full max-w-lg text-left text-sm">
          <thead>
            <tr className="border-b border-line text-xs uppercase tracking-[0.1em] text-muted">
              <th className="py-2">Contato</th>
              <th className="py-2">Saldo</th>
              <th className="py-2">Comprado</th>
              <th className="py-2">Consumido</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {customers.map((c) => (
              <tr key={c.contact_phone_number} className="border-b border-line">
                <td className="py-3">{formatPhone(c.contact_phone_number)}</td>
                <td className="py-3 font-mono">{formatCredits(c.credit_balance)}</td>
                <td className="py-3 font-mono text-muted">{formatCredits(c.total_purchased)}</td>
                <td className="py-3 font-mono text-muted">{formatCredits(c.total_consumed)}</td>
                <td className="py-3 text-right">
                  <button
                    type="button"
                    disabled={removing === c.contact_phone_number}
                    onClick={() => void handleRemoveCredits(c.contact_phone_number)}
                    className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger disabled:opacity-50"
                  >
                    {removing === c.contact_phone_number ? "Removendo..." : "Remover créditos"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
