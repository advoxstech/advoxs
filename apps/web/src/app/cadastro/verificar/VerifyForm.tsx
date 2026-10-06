"use client";

import { useActionState } from "react";

import { confirmEmail, type VerifyState } from "./actions";

export function VerifyForm({ token }: { token: string }) {
  const [state, action, pending] = useActionState(confirmEmail, { error: null } as VerifyState);
  return (
    <form action={action} className="mt-6">
      <input type="hidden" name="token" value={token} />
      {state.error && <p role="alert" className="mb-4 text-sm text-danger">{state.error}</p>}
      <button type="submit" disabled={pending} className="rounded-xl bg-ink px-5 py-3 font-semibold text-surface disabled:opacity-60">
        {pending ? "Confirmando…" : "Confirmar e-mail"}
      </button>
    </form>
  );
}
