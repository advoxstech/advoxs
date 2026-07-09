import Link from "next/link";

import { SignupForm } from "@/components/SignupForm";
import { API_URL } from "@/lib/backend";
import type { CreditPackage } from "@/lib/types";

async function getPackages(): Promise<CreditPackage[]> {
  try {
    const response = await fetch(`${API_URL}/api/v1/credit-packages`, { cache: "no-store" });
    if (!response.ok) return [];
    return response.json();
  } catch {
    return [];
  }
}

export default async function HomePage() {
  const packages = await getPackages();

  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="w-full max-w-md">
        <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-muted">
          Plataforma de agentes de IA
        </p>
        <h1 className="mt-2 font-display text-5xl font-semibold text-ink">
          Advoxs<span className="text-accent">.</span>
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          Agentes de IA que atendem os clientes do seu escritório pelo WhatsApp. Escolha um
          plano e comece agora.
        </p>

        <hr className="my-8 border-line" />

        <SignupForm packages={packages} />

        <p className="mt-6 text-center text-sm text-muted">
          Já tem conta?{" "}
          <Link href="/login" className="text-accent hover:underline">
            Entrar
          </Link>
        </p>
      </div>
    </main>
  );
}
