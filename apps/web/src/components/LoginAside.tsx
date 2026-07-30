export function LoginAside() {
  return (
    <aside className="relative flex flex-col justify-center gap-12 overflow-hidden bg-gradient-to-b from-nav-bg to-nav-bg-2 px-8 py-10 text-nav-ink lg:px-14 lg:py-16">
      <div
        aria-hidden
        className="pointer-events-none absolute -right-44 -top-44 h-[460px] w-[460px] rounded-full bg-nav-active/45 blur-3xl"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-56 -left-40 h-[520px] w-[520px] rounded-full bg-brass/30 blur-3xl"
      />

      <div className="relative z-10 flex flex-col gap-6">
        <div className="font-display text-5xl font-semibold leading-none tracking-tight">
          Advoxs<span className="text-auth-accent">.</span>
        </div>
        <p className="max-w-[32ch] text-lg leading-relaxed text-nav-ink-muted">
          Acompanhe os atendimentos dos seus agentes no WhatsApp e assuma a conversa quando
          precisar.
        </p>
      </div>
    </aside>
  );
}
