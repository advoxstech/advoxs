"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type { OnboardingProgress } from "@/lib/onboarding";

export function OnboardingGate({ children }: { children: React.ReactNode }) {
  const [progress, setProgress] = useState<OnboardingProgress | null>(null);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const response = await backendFetch("onboarding");
        if (response.ok) setProgress(await response.json());
      } catch {
        // O acompanhamento é opcional. Falhas nunca escondem nem bloqueiam o painel.
      }
    }
    void load();
  }, []);

  async function dismiss() {
    setHidden(true);
    try {
      await backendFetch("onboarding/complete", { method: "POST" });
    } catch {
      // Oculto nesta sessão; se o POST falhar, poderá reaparecer mais tarde.
    }
  }

  const showReminder = progress && !progress.completed && !hidden;

  return (
    <>
      {showReminder && (
        <section className="mx-6 mt-6 rounded-sm border border-line bg-surface p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted">
                Configuração inicial · {progress.completed_steps} de {progress.total_steps}
              </p>
              <h2 className="mt-2 font-display text-xl font-semibold text-ink">
                {progress.main_configuration_complete
                  ? "Configuração principal concluída"
                  : "Continue configurando no seu ritmo"}
              </h2>
              <p className="mt-1 max-w-2xl text-sm text-muted">
                Este acompanhamento é opcional e não impede o acesso nem o atendimento.
              </p>
            </div>
            <div className="flex items-center gap-3">
              <Link
                href="/boas-vindas"
                className="rounded-sm bg-accent px-4 py-2 text-sm font-medium text-surface transition-colors hover:bg-ink"
              >
                Ver progresso
              </Link>
              <button
                type="button"
                onClick={() => void dismiss()}
                className="text-sm text-muted underline transition-colors hover:text-ink"
              >
                Ocultar
              </button>
            </div>
          </div>
        </section>
      )}
      {children}
    </>
  );
}
