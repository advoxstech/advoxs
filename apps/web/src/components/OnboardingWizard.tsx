"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type { OnboardingProgress, OnboardingStep } from "@/lib/onboarding";

const sectionLabels: Record<OnboardingStep["kind"], string> = {
  main: "Configuração principal",
  recommended: "Recomendado",
  milestone: "Primeiro atendimento",
};

export function OnboardingWizard() {
  const [progress, setProgress] = useState<OnboardingProgress | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [leaving, setLeaving] = useState(false);

  const loadProgress = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const response = await backendFetch("onboarding");
      if (!response.ok) throw new Error("Não foi possível carregar o progresso");
      setProgress(await response.json());
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProgress();
    window.addEventListener("focus", loadProgress);
    return () => window.removeEventListener("focus", loadProgress);
  }, [loadProgress]);

  async function continueLater() {
    if (leaving) return;
    setLeaving(true);
    try {
      await backendFetch("onboarding/complete", { method: "POST" });
    } catch {
      // A navegação continua: o onboarding não pode bloquear o escritório.
    }
    window.location.assign("/inicio");
  }

  return (
    <main className="min-h-screen bg-ground px-6 py-10">
      <div className="mx-auto w-full max-w-3xl">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted">
          Configuração inicial
        </p>
        <h1 className="mt-3 font-display text-3xl font-semibold text-ink">
          Prepare seu primeiro atendimento
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-muted">
          Acompanhe o que já está pronto e abra diretamente cada configuração pendente.
          Você pode sair desta página a qualquer momento: este roteiro não bloqueia o painel
          nem o atendimento dos clientes.
        </p>

        {loading && !progress && (
          <p className="mt-8 text-sm text-muted">Verificando sua configuração...</p>
        )}

        {error && !progress && (
          <section className="mt-8 rounded-sm border border-line bg-surface p-5">
            <p className="text-sm text-ink">Não foi possível consultar o progresso agora.</p>
            <button
              type="button"
              onClick={() => void loadProgress()}
              className="mt-3 text-sm text-accent underline"
            >
              Tentar novamente
            </button>
          </section>
        )}

        {progress && (
          <>
            <section className="mt-8 rounded-sm border border-line bg-surface p-5">
              <div className="flex items-end justify-between gap-4">
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted">
                    Progresso real
                  </p>
                  <p className="mt-2 font-display text-2xl font-semibold text-ink">
                    {progress.completed_steps} de {progress.total_steps} etapas
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void loadProgress()}
                  disabled={loading}
                  className="text-sm text-accent underline disabled:opacity-50"
                >
                  {loading ? "Atualizando..." : "Atualizar progresso"}
                </button>
              </div>
              <div className="mt-4 h-2 overflow-hidden rounded-full bg-ground">
                <div
                  className="h-full bg-accent transition-all"
                  style={{
                    width: `${(progress.completed_steps / progress.total_steps) * 100}%`,
                  }}
                />
              </div>
              {progress.main_configuration_complete && (
                <p className="mt-4 rounded-sm bg-accent-soft px-4 py-3 text-sm text-accent">
                  A configuração principal está pronta. As demais etapas ajudam a validar e
                  acompanhar o primeiro atendimento.
                </p>
              )}
            </section>

            <div className="mt-6 flex flex-col gap-6">
              {(["main", "recommended", "milestone"] as const).map((kind) => {
                const steps = progress.steps.filter((step) => step.kind === kind);
                if (steps.length === 0) return null;
                return (
                  <section key={kind}>
                    <h2 className="font-mono text-[11px] uppercase tracking-[0.16em] text-muted">
                      {sectionLabels[kind]}
                    </h2>
                    <div className="mt-2 divide-y divide-line rounded-sm border border-line bg-surface">
                      {steps.map((step) => (
                        <div
                          key={step.key}
                          className="flex flex-wrap items-center justify-between gap-4 p-4"
                        >
                          <div className="flex min-w-0 items-start gap-3">
                            <span
                              aria-label={step.completed ? "Concluído" : "Pendente"}
                              className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                                step.completed
                                  ? "bg-accent text-surface"
                                  : "border border-line text-muted"
                              }`}
                            >
                              {step.completed ? "✓" : "·"}
                            </span>
                            <div>
                              <p className="text-sm font-medium text-ink">{step.label}</p>
                              <p className="mt-1 text-xs leading-relaxed text-muted">
                                {step.description}
                              </p>
                            </div>
                          </div>
                          <Link
                            href={step.action_href}
                            className="shrink-0 text-sm text-accent underline"
                          >
                            {step.action_label}
                          </Link>
                        </div>
                      ))}
                    </div>
                  </section>
                );
              })}
            </div>
          </>
        )}

        <footer className="mt-8 flex flex-wrap items-center gap-4 border-t border-line pt-5">
          <button
            type="button"
            onClick={() => void continueLater()}
            disabled={leaving}
            className="rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink disabled:opacity-50"
          >
            Continuar para o painel
          </button>
          <p className="text-xs text-muted">
            Você pode voltar por este acompanhamento enquanto ele estiver visível no início.
          </p>
        </footer>
      </div>
    </main>
  );
}
