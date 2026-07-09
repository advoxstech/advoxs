"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";

const MAX_ATTEMPTS = 8;

export function CreditosSucessoPanel({
  sessionId,
  pollMs = 2000,
}: {
  sessionId: string | null;
  pollMs?: number;
}) {
  const [ready, setReady] = useState(false);
  const [attempts, setAttempts] = useState(0);

  async function checkStatus() {
    if (!sessionId) return;
    try {
      const response = await backendFetch(
        `billing/status?session_id=${encodeURIComponent(sessionId)}`,
      );
      if (response.ok) {
        const body = await response.json();
        if (body.ready) {
          setReady(true);
          return;
        }
      }
    } catch {
      // rede instável durante o polling — só tenta de novo no próximo ciclo
    }
    setAttempts((prev) => prev + 1);
  }

  useEffect(() => {
    void checkStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId || ready || attempts >= MAX_ATTEMPTS) return;
    const interval = setInterval(() => void checkStatus(), pollMs);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, ready, attempts, pollMs]);

  const settled = ready || attempts >= MAX_ATTEMPTS || !sessionId;

  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="w-full max-w-sm text-center">
        <h1 className="font-display text-3xl font-semibold text-ink">
          {settled ? "Pagamento confirmado" : "Confirmando seu pagamento…"}
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          {settled
            ? "Seu saldo de créditos já foi atualizado."
            : "Isso leva só alguns segundos."}
        </p>
        {settled && (
          <Link
            href="/conversas"
            className="mt-6 inline-block rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink"
          >
            Voltar para o início
          </Link>
        )}
      </div>
    </div>
  );
}
