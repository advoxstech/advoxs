"use client";

import { useEffect, useState } from "react";

import { autoLogin } from "@/app/cadastro/actions";
import { backendFetch } from "@/lib/client-api";

const MAX_ATTEMPTS = 8;

export function SignupSuccessPanel({
  sessionId,
  pollMs = 2000,
}: {
  sessionId: string | null;
  pollMs?: number;
}) {
  const [ready, setReady] = useState(false);
  const [attempts, setAttempts] = useState(0);
  const [loggingIn, setLoggingIn] = useState(false);

  async function tryAutoLogin(token: string) {
    setLoggingIn(true);
    try {
      const result = await autoLogin(token);
      if (result?.error) {
        // Token rejeitado (expirado/reusado): volta pro fallback com o botão.
        setLoggingIn(false);
      }
      // Sem erro: a action redirecionou pro /inicio — o Next cuida da
      // navegação e este componente sai de cena.
    } catch {
      // Rejeição inesperada da action (ex: rede): volta pro fallback.
      setLoggingIn(false);
    }
  }

  async function checkStatus() {
    if (!sessionId) return;
    try {
      const response = await backendFetch(
        `signup/status?session_id=${encodeURIComponent(sessionId)}`,
      );
      if (response.ok) {
        const body = await response.json();
        if (body.ready) {
          setReady(true);
          if (body.login_token) {
            void tryAutoLogin(body.login_token);
          }
          return;
        }
      }
    } catch {
      // Rede instável durante o polling — só tenta de novo no próximo ciclo.
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
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm text-center">
        <h1 className="font-display text-3xl font-semibold text-ink">
          {settled ? "Pagamento confirmado" : "Confirmando seu pagamento…"}
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          {loggingIn
            ? "Entrando na sua conta…"
            : settled
              ? "Sua conta está pronta. Você já pode entrar com o e-mail e a senha que cadastrou."
              : "Isso leva só alguns segundos."}
        </p>
        {settled && !loggingIn && (
          <a
            href="/login"
            className="mt-6 inline-block rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink"
          >
            Ir para o login
          </a>
        )}
      </div>
    </main>
  );
}
