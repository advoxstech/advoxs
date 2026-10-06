"use client";

import { useActionState } from "react";

import { resendVerification, type ResendState } from "./actions";

const initialState: ResendState = { error: null, sent: false };

export function ResendForm() {
  const [state, action, pending] = useActionState(resendVerification, initialState);
  return (
    <form action={action} className="mt-7 flex flex-col gap-3 text-left">
      <label htmlFor="resend-email" className="text-sm font-semibold text-ink">
        Reenviar para
      </label>
      <input
        id="resend-email"
        name="email"
        type="email"
        required
        autoComplete="email"
        placeholder="voce@escritorio.com.br"
        className="h-[50px] rounded-xl border border-line bg-surface px-4 text-[15px] text-ink"
      />
      {state.error && <p role="alert" className="text-sm text-danger">{state.error}</p>}
      {state.sent && <p role="status" className="text-sm text-ink">Se houver um cadastro pendente, enviamos um novo link.</p>}
      <button type="submit" disabled={pending} className="h-[50px] rounded-xl bg-ink px-4 font-semibold text-surface disabled:opacity-60">
        {pending ? "Reenviando…" : "Reenviar confirmação"}
      </button>
    </form>
  );
}
