import { LoginForm } from "./LoginForm";

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-muted">
          Painel do escritório
        </p>
        <h1 className="mt-2 font-display text-5xl font-semibold">
          Advoxs<span className="text-accent">.</span>
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          Acompanhe os atendimentos dos seus agentes no WhatsApp e assuma a
          conversa quando precisar.
        </p>

        <hr className="my-8 border-line" />

        <LoginForm />
      </div>
    </main>
  );
}
