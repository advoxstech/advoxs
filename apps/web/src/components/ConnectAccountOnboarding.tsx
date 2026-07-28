"use client";

import { loadConnectAndInitialize } from "@stripe/connect-js";
import { useEffect, useRef, useState } from "react";

import { backendFetch } from "@/lib/client-api";

function extractErrorDetail(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export function ConnectAccountOnboarding() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    // Guarda o client_secret já obtido — Connect.js pode chamar
    // `fetchClientSecret` de novo (ex: refresh de sessão), e não queremos
    // criar uma Account Session nova a cada chamada.
    let cachedSecret: string | null = null;

    async function fetchClientSecret(): Promise<string> {
      if (cachedSecret) return cachedSecret;
      const response = await backendFetch("end-customer-billing/connect-account", {
        method: "POST",
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(
          extractErrorDetail(body, "Falha ao iniciar a configuração de pagamentos."),
        );
      }
      cachedSecret = body.client_secret as string;
      return cachedSecret;
    }

    async function mount() {
      try {
        // Busca (e valida) o client_secret antes de inicializar o Connect.js
        // — assim um erro do backend aparece na tela em vez de ficar preso
        // dentro da inicialização assíncrona da lib.
        await fetchClientSecret();
        const connectInstance = await loadConnectAndInitialize({
          publishableKey: process.env.NEXT_PUBLIC_STRIPE_CONNECT_PUBLISHABLE_KEY ?? "",
          fetchClientSecret,
        });
        if (!active || !containerRef.current) return;
        const onboarding = connectInstance.create("account-onboarding");
        containerRef.current.appendChild(onboarding);
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Falha de conexão — tente novamente.");
      }
    }

    void mount();
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="max-w-xl">
      {error && (
        <p role="alert" className="mb-4 text-sm text-danger">
          {error}
        </p>
      )}
      <div ref={containerRef} />
    </div>
  );
}
