"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type { CreditPackage } from "@/lib/types";

export function CreditosPanel({ packages }: { packages: CreditPackage[] }) {
  const [balance, setBalance] = useState<number | null>(null);
  const [purchasingId, setPurchasingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadBalance() {
      const response = await backendFetch("billing/balance");
      if (response.ok) {
        const body = await response.json();
        setBalance(body.credit_balance);
      }
    }
    void loadBalance();
  }, []);

  async function handleComprar(packageId: string) {
    setError(null);
    setPurchasingId(packageId);
    try {
      const response = await backendFetch("billing/checkout", {
        method: "POST",
        body: JSON.stringify({ credit_package_id: packageId }),
      });
      if (!response.ok) {
        setError("Não foi possível iniciar o pagamento. Tente novamente.");
        setPurchasingId(null);
        return;
      }
      const body = await response.json();
      window.location.href = body.checkout_url;
      // Sem reset de purchasingId aqui — a navegação real vai destruir a página;
      // manter o botão desabilitado evita um segundo clique nessa janela.
    } catch {
      setError("Não foi possível iniciar o pagamento. Tente novamente.");
      setPurchasingId(null);
    }
  }

  return (
    <div className="flex flex-col gap-8 p-8">
      <div>
        <p className="font-mono text-[11px] uppercase tracking-[0.15em] text-muted">
          Saldo atual
        </p>
        <p className="mt-1 font-display text-4xl font-semibold text-ink">
          {balance === null ? "…" : `${balance} créditos`}
        </p>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="flex flex-col gap-3">
        {packages.map((pkg) => (
          <div
            key={pkg.id}
            className="flex items-center justify-between gap-4 rounded-sm border border-line bg-surface px-4 py-3 text-sm"
          >
            <span>
              <span className="font-medium text-ink">{pkg.name}</span>{" "}
              <span className="text-muted">— {pkg.credits_granted} créditos</span>
            </span>
            <div className="flex items-center gap-3">
              <span className="font-mono text-xs text-muted">
                R$ {Number(pkg.price_brl).toFixed(2)}
              </span>
              <button
                type="button"
                onClick={() => void handleComprar(pkg.id)}
                disabled={purchasingId !== null}
                className="rounded-sm bg-accent px-3 py-1.5 text-sm font-medium text-surface disabled:opacity-60"
              >
                {purchasingId === pkg.id ? "Redirecionando…" : "Comprar"}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
