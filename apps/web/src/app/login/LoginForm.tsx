"use client";

import Link from "next/link";
import { useActionState, useState } from "react";

import { login, type LoginState } from "./actions";

const initialState: LoginState = { error: null };

export function LoginForm() {
  const [state, formAction, pending] = useActionState(login, initialState);
  const [showPassword, setShowPassword] = useState(false);

  return (
    <form action={formAction} className="flex w-full max-w-[424px] flex-col gap-7">
      <header className="flex flex-col gap-2.5">
        <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-muted">
          Painel do escritório
        </span>
        <h1 className="font-display text-4xl font-semibold tracking-tight text-ink">
          Entre na sua conta
        </h1>
        <p className="text-[15px] leading-relaxed text-muted">
          Ainda não tem acesso?{" "}
          <Link href="/" className="font-semibold text-auth-accent hover:underline">
            Criar conta
          </Link>
        </p>
      </header>

      <div className="flex flex-col gap-5">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="email" className="text-[13px] font-semibold text-ink">
            E-mail
          </label>
          <input
            id="email"
            name="email"
            type="email"
            required
            autoComplete="email"
            placeholder="voce@escritorio.com.br"
            className="h-[52px] rounded-xl border border-line bg-surface px-4 text-[15px] text-ink"
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
              required
              autoComplete="current-password"
              placeholder="Sua senha"
              className="h-[52px] flex-1 rounded-xl border border-line bg-surface px-4 pr-[88px] text-[15px] text-ink"
            />
            <button
              type="button"
              onClick={() => setShowPassword((value) => !value)}
              className="absolute right-2 top-2 h-[34px] rounded-lg bg-auth-accent-soft px-3 font-mono text-[11px] font-medium uppercase tracking-[0.08em] text-auth-accent transition-colors hover:bg-auth-accent/20"
            >
              {showPassword ? "Ocultar" : "Mostrar"}
            </button>
          </div>
        </div>
      </div>

      {state.error ? (
        <p role="alert" className="border-l-2 border-danger pl-3 text-sm text-danger">
          {state.error}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={pending}
        className="h-[56px] rounded-2xl bg-gradient-to-b from-auth-accent to-ink text-base font-semibold text-surface shadow-lg transition-transform hover:-translate-y-px disabled:opacity-60"
      >
        {pending ? "Entrando…" : "Entrar no painel"}
      </button>
    </form>
  );
}
