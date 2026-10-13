"use client";
import { logout } from "@/app/conversas/actions";
import { useState } from "react";
import { TenantShell } from "@/components/TenantShell";
import { teamRequest } from "@/components/TeamPanel";

export default function Page() {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <TenantShell active="minha-conta">
      <main className="p-6">
        <h1 className="font-display text-3xl mb-5">Minha conta</h1>
        <form
          className="max-w-md space-y-4"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            const data = Object.fromEntries(new FormData(e.currentTarget));
            try {
              await teamRequest("profile/password", {
                method: "POST",
                body: JSON.stringify(data),
              });
              await logout();
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <label className="block">
            Senha atual
            <input
              className="border border-line p-2 block w-full"
              name="current_password"
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          <label className="block">
            Nova senha
            <input
              className="border border-line p-2 block w-full"
              name="new_password"
              type="password"
              minLength={8}
              maxLength={72}
              autoComplete="new-password"
              required
            />
          </label>
          <button
            disabled={busy}
            className="bg-accent text-white rounded px-4 py-2"
          >
            Alterar senha
          </button>
          {error && <p role="alert">{error}</p>}
        </form>
      </main>
    </TenantShell>
  );
}
