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

  return (
    <div className="mt-8">
      <h2 className="font-display text-lg font-semibold text-ink">Clientes finais</h2>
      {!loaded ? (
        <p className="mt-3 text-sm text-muted">Carregando...</p>
      ) : customers.length === 0 ? (
        <p className="mt-3 text-sm text-muted">Nenhum cliente comprou créditos ainda.</p>
      ) : (
        <table className="mt-4 w-full max-w-md text-left text-sm">
          <thead>
            <tr className="border-b border-line text-xs uppercase tracking-[0.1em] text-muted">
              <th className="py-2">Contato</th>
              <th className="py-2">Saldo</th>
              <th className="py-2">Comprado</th>
              <th className="py-2">Consumido</th>
            </tr>
          </thead>
          <tbody>
            {customers.map((c) => (
              <tr key={c.contact_phone_number} className="border-b border-line">
                <td className="py-3">{formatPhone(c.contact_phone_number)}</td>
                <td className="py-3 font-mono">{formatCredits(c.credit_balance)}</td>
                <td className="py-3 font-mono text-muted">{formatCredits(c.total_purchased)}</td>
                <td className="py-3 font-mono text-muted">{formatCredits(c.total_consumed)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
