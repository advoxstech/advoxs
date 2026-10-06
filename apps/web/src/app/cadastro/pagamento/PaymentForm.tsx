"use client";

import { useActionState } from "react";

import { startPayment, type PaymentState } from "./actions";

export function PaymentForm() {
  const [state, action, pending] = useActionState(startPayment, { error: null } as PaymentState);
  return (
    <form action={action} className="mt-6">
      {state.error && <p role="alert" className="mb-4 text-sm text-danger">{state.error}</p>}
      <button type="submit" disabled={pending} className="rounded-xl bg-ink px-5 py-3 font-semibold text-surface disabled:opacity-60">
        {pending ? "Preparando pagamento…" : "Ir para o pagamento"}
      </button>
    </form>
  );
}
