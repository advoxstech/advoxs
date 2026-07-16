"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { backendFetch } from "@/lib/client-api";

/** Gate do tutorial de primeira abertura: tenant sem onboarding completado é
 * levado pro wizard /boas-vindas. Fail-open — erro na checagem nunca tranca o
 * painel (o tutorial é nice-to-have). */
export function OnboardingGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<"checking" | "allowed">("checking");

  useEffect(() => {
    let cancelled = false;
    async function check() {
      try {
        const response = await backendFetch("onboarding");
        if (response.ok) {
          const body = await response.json();
          if (!cancelled && body.completed === false) {
            router.replace("/boas-vindas");
            return;
          }
        }
      } catch {
        // fail-open
      }
      if (!cancelled) {
        setState("allowed");
      }
    }
    void check();
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (state === "checking") {
    // div, não <main>: o gate vive DENTRO do <main> da página /inicio —
    // um segundo landmark main seria HTML inválido.
    return (
      <div className="flex flex-1 items-center justify-center bg-ground text-sm text-muted">
        Carregando...
      </div>
    );
  }
  return <>{children}</>;
}
