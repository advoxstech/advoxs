"use client";

import { useActionState } from "react";

import { signup, type SignupState } from "@/app/actions";
import type { CreditPackage } from "@/lib/types";

const initialState: SignupState = { error: null };

export function SignupForm({ packages }: { packages: CreditPackage[] }) {
  const [state, formAction, pending] = useActionState(signup, initialState);

  return (
    <form action={formAction} className="flex flex-col gap-6">
      <fieldset className="flex flex-col gap-3">
        <legend className="text-sm font-medium text-ink">Escolha um plano</legend>
        {packages.map((pkg, index) => (
          <label
            key={pkg.id}
            className="flex items-center justify-between gap-4 rounded-sm border border-line bg-surface px-4 py-3 text-sm"
          >
            <span className="flex items-center gap-3">
              <input type="radio" name="credit_package_id" value={pkg.id} required defaultChecked={index === 0} />
              <span>
                <span className="font-medium text-ink">{pkg.name}</span>{" "}
                <span className="text-muted">— {pkg.credits_granted} créditos</span>
              </span>
            </span>
            <span className="font-mono text-xs text-muted">
              R$ {Number(pkg.price_brl).toFixed(2)}
            </span>
          </label>
        ))}
      </fieldset>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="tenant_name" className="text-sm font-medium text-ink">
          Nome do escritório
        </label>
        <input
          id="tenant_name"
          name="tenant_name"
          type="text"
          required
          className="rounded-sm border border-line bg-surface px-3 py-2.5 text-sm"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="email" className="text-sm font-medium text-ink">
          E-mail
        </label>
        <input
          id="email"
          name="email"
          type="email"
          required
          autoComplete="email"
          className="rounded-sm border border-line bg-surface px-3 py-2.5 text-sm"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="password" className="text-sm font-medium text-ink">
          Senha
        </label>
        <input
          id="password"
          name="password"
          type="password"
          required
          minLength={8}
          autoComplete="new-password"
          className="rounded-sm border border-line bg-surface px-3 py-2.5 text-sm"
        />
      </div>

      {state.error ? (
        <p role="alert" className="border-l-2 border-danger pl-3 text-sm text-danger">
          {state.error}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={pending}
        className="mt-1 rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink disabled:opacity-60"
      >
        {pending ? "Preparando pagamento…" : "Assinar e pagar"}
      </button>
    </form>
  );
}
