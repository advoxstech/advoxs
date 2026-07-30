"use client";

import { useActionState, useState } from "react";

import { signup, type SignupState } from "@/app/actions";
import { formatBRL } from "@/lib/format";
import type { CreditPackage } from "@/lib/types";

const initialState: SignupState = { error: null };

function passwordStrength(password: string): { pct: number; label: string } {
  if (!password) return { pct: 0, label: "—" };
  let score = 0;
  if (password.length >= 8) score++;
  if (password.length >= 12) score++;
  if (/[A-Z]/.test(password) && /[a-z]/.test(password)) score++;
  if (/[0-9]/.test(password)) score++;
  if (/[^A-Za-z0-9]/.test(password)) score++;
  if (score <= 2) return { pct: 33, label: "Fraca" };
  if (score === 3) return { pct: 66, label: "Média" };
  return { pct: 100, label: "Forte" };
}

const STRENGTH_BAR_CLASS: Record<string, string> = {
  Fraca: "bg-danger",
  Média: "bg-signup-accent",
  Forte: "bg-ink",
};

export function SignupForm({ packages }: { packages: CreditPackage[] }) {
  const [state, formAction, pending] = useActionState(signup, initialState);
  const [selectedId, setSelectedId] = useState(packages[0]?.id ?? "");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);

  const selected = packages.find((pkg) => pkg.id === selectedId) ?? packages[0];
  const strength = passwordStrength(password);
  const selectedPrice = selected ? `R$ ${formatBRL(Number(selected.price_brl))}` : null;

  return (
    <form action={formAction} className="flex w-full max-w-[520px] flex-col gap-8">
      <header className="flex flex-col gap-2.5">
        <div className="flex items-center justify-between gap-4">
          <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted">
            Criar conta
          </span>
          <span className="text-[13px] text-muted">
            Já tem conta?{" "}
            <a href="/login" className="font-semibold text-signup-accent hover:underline">
              Entrar
            </a>
          </span>
        </div>
        <h1 className="font-display text-4xl font-semibold tracking-tight text-ink">
          Escolha um plano e comece agora
        </h1>
      </header>

      <fieldset className="flex flex-col gap-3">
        <div className="flex items-baseline justify-between gap-3">
          <legend className="text-sm font-bold tracking-wide text-ink">Plano</legend>
          <span className="text-xs text-muted">Pagamento único · sem mensalidade</span>
        </div>
        <div className="flex flex-col gap-2.5">
          {packages.map((pkg) => {
            const active = pkg.id === selectedId;
            const perCredit = (Number(pkg.price_brl) / pkg.credits_granted)
              .toFixed(3)
              .replace(".", ",");
            return (
              <label
                key={pkg.id}
                className={`relative flex cursor-pointer items-center gap-3.5 rounded-2xl border bg-surface px-4 py-4 transition-colors ${
                  active
                    ? "border-signup-accent ring-4 ring-signup-accent/15"
                    : "border-line hover:border-muted"
                }`}
              >
                <input
                  type="radio"
                  name="credit_package_id"
                  value={pkg.id}
                  checked={active}
                  onChange={() => setSelectedId(pkg.id)}
                  required
                  className="peer sr-only"
                />
                <span
                  className={`grid h-5 w-5 flex-none place-items-center rounded-full border peer-focus-visible:ring-2 peer-focus-visible:ring-signup-accent peer-focus-visible:ring-offset-2 ${
                    active ? "border-signup-accent" : "border-line"
                  }`}
                >
                  <span
                    className={`h-2.5 w-2.5 rounded-full bg-signup-accent transition-transform ${
                      active ? "scale-100" : "scale-0"
                    }`}
                  />
                </span>
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="text-base font-bold text-ink">{pkg.name}</span>
                  <span className="whitespace-nowrap text-[13px] text-muted">
                    {pkg.credits_granted.toLocaleString("pt-BR")} créditos · R$ {perCredit}/crédito
                  </span>
                </span>
                <span className="ml-auto flex-none whitespace-nowrap font-mono text-[17px] font-medium tracking-tight text-ink">
                  R$ {formatBRL(Number(pkg.price_brl))}
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="flex flex-col gap-4">
        <legend className="text-sm font-bold tracking-wide text-ink">Seus dados</legend>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="tenant_name" className="text-[13px] font-semibold text-ink">
            Nome do escritório
          </label>
          <input
            id="tenant_name"
            name="tenant_name"
            type="text"
            required
            placeholder="Ex.: Vieira &amp; Associados"
            className="h-[50px] rounded-xl border border-line bg-surface px-4 text-[15px] text-ink"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="email" className="text-[13px] font-semibold text-ink">
            E-mail profissional
          </label>
          <input
            id="email"
            name="email"
            type="email"
            required
            autoComplete="email"
            placeholder="voce@escritorio.com.br"
            className="h-[50px] rounded-xl border border-line bg-surface px-4 text-[15px] text-ink"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="password" className="text-[13px] font-semibold text-ink">
            Senha
          </label>
          <div className="relative flex">
            <input
              id="password"
              name="password"
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              minLength={8}
              autoComplete="new-password"
              placeholder="Mínimo 8 caracteres"
              className="h-[50px] flex-1 rounded-xl border border-line bg-surface px-4 pr-[84px] text-[15px] text-ink"
            />
            <button
              type="button"
              onClick={() => setShowPassword((value) => !value)}
              className="absolute right-2 top-2 h-[34px] rounded-lg bg-signup-accent-soft px-3 font-mono text-[12px] font-bold uppercase tracking-[0.04em] text-signup-accent transition-colors hover:bg-signup-accent/20"
            >
              {showPassword ? "Ocultar" : "Mostrar"}
            </button>
          </div>
          <div className="flex items-center gap-2">
            <span className="h-1 flex-1 overflow-hidden rounded-full bg-line">
              <span
                className={`block h-full rounded-full transition-all ${
                  STRENGTH_BAR_CLASS[strength.label] ?? ""
                }`}
                style={{ width: `${strength.pct}%` }}
              />
            </span>
            <span className="min-w-[60px] text-right text-xs font-semibold text-muted">
              {strength.label}
            </span>
          </div>
        </div>
      </fieldset>

      <section className="flex flex-col gap-3 rounded-2xl border border-line bg-surface px-5 py-5 shadow-sm">
        <div className="flex items-baseline justify-between gap-3">
          <span className="whitespace-nowrap text-sm text-muted">
            Plano {selected?.name} · {selected?.credits_granted.toLocaleString("pt-BR")} créditos
          </span>
          <span className="font-mono text-sm text-ink">{selectedPrice ?? "—"}</span>
        </div>
        <div className="h-px bg-line" />
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-[15px] font-bold text-ink">Total hoje</span>
          <span className="font-mono text-2xl font-medium tracking-tight text-ink">
            {selectedPrice ?? "—"}
          </span>
        </div>
      </section>

      {state.error ? (
        <p role="alert" className="border-l-2 border-danger pl-3 text-sm text-danger">
          {state.error}
        </p>
      ) : null}

      <div className="flex flex-col gap-3.5">
        <button
          type="submit"
          disabled={pending}
          className="h-[58px] rounded-2xl bg-gradient-to-b from-signup-accent to-ink text-base font-bold text-surface shadow-lg transition-transform hover:-translate-y-px disabled:opacity-60"
        >
          {pending ? "Preparando pagamento…" : `Comprar por ${selectedPrice ?? ""}`}
        </button>
        <div className="flex flex-wrap items-center justify-center gap-4">
          <span className="flex items-center gap-1.5 text-xs text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-muted" />
            Pagamento criptografado
          </span>
          <span className="flex items-center gap-1.5 text-xs text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-muted" />
            Recompre quando quiser
          </span>
        </div>
      </div>
    </form>
  );
}
