"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

export default function Page() {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const value = new URLSearchParams(window.location.hash.slice(1)).get(
      "token",
    );
    if (value) setToken(value);
    window.history.replaceState(null, "", window.location.pathname);
  }, []);
  return (
    <main className="min-h-screen flex items-center justify-center p-6">
      <div className="max-w-md w-full bg-surface border border-line p-8 rounded space-y-5">
        <h1 className="font-display text-3xl">Convite para o escritório</h1>
        {done ? (
          <>
            <p>
              Conta criada. Entre com o e-mail que recebeu o convite e sua nova
              senha.
            </p>
            <Link href="/login" className="text-accent underline">
              Ir para o login
            </Link>
          </>
        ) : (
          <form
            className="space-y-4"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError("");
              const data = new FormData(e.currentTarget);
              try {
                const r = await fetch("/api/convite", {
                  method: "POST",
                  headers: { "content-type": "application/json" },
                  body: JSON.stringify({
                    token,
                    password: data.get("password"),
                  }),
                });
                const body = await r.json();
                if (!r.ok)
                  throw new Error(
                    typeof body.detail === "string"
                      ? body.detail
                      : "Não foi possível aceitar o convite.",
                  );
                setDone(true);
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <p>Defina uma senha para acessar o escritório que convidou você.</p>
            <label className="block">
              Nova senha
              <input
                name="password"
                type="password"
                autoComplete="new-password"
                minLength={8}
                maxLength={72}
                required
                className="block w-full border border-line rounded p-3 mt-2"
              />
            </label>
            <button
              disabled={busy || !token}
              className="bg-accent text-white rounded px-4 py-3 disabled:opacity-50"
            >
              {busy ? "Criando conta…" : "Aceitar convite"}
            </button>
            {!token && <p>Abra o link completo recebido por e-mail.</p>}
          </form>
        )}
        {error && (
          <p role="alert" className="text-danger">
            {error}
          </p>
        )}
      </div>
    </main>
  );
}
