"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";

export function LowBalanceBanner() {
  const [lowBalance, setLowBalance] = useState(false);

  useEffect(() => {
    async function loadBalance() {
      try {
        const response = await backendFetch("billing/balance");
        if (response.ok) {
          const body = await response.json();
          setLowBalance(body.credit_balance <= 0);
        }
      } catch {
        // Fail-safe silencioso — sem saldo confirmado, não exibe o aviso.
      }
    }
    void loadBalance();
  }, []);

  if (!lowBalance) return null;

  return (
    <div className="flex items-center justify-between gap-4 border-b border-danger bg-danger/10 px-6 py-2.5 text-sm text-danger">
      <span>
        Seu saldo de créditos está esgotado — o atendimento automático está pausado.
      </span>
      <Link href="/creditos" className="font-medium underline hover:no-underline">
        Comprar créditos
      </Link>
    </div>
  );
}
