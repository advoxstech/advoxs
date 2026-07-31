const BENEFITS = [
  {
    title: "Resposta imediata, sempre",
    description: "Seu cliente nunca espera: o agente responde em segundos, todos os dias.",
  },
  {
    title: "Créditos que não expiram",
    description: "Você compra créditos e paga só pelo uso real — sem contrato de fidelidade.",
  },
  {
    title: "Comece agora mesmo",
    description: "Escolha o pacote, finalize o pagamento e já entre direto no seu painel.",
  },
];

export function SignupAside() {
  return (
    <aside className="relative flex flex-col justify-start gap-12 overflow-hidden bg-gradient-to-b from-nav-bg to-nav-bg-2 px-8 py-10 text-nav-ink lg:sticky lg:top-0 lg:h-screen lg:self-start lg:px-14 lg:py-14">
      <div
        aria-hidden
        className="pointer-events-none absolute -right-44 -top-44 h-[460px] w-[460px] rounded-full bg-nav-active/45 blur-3xl"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-56 -left-40 h-[520px] w-[520px] rounded-full bg-brass/30 blur-3xl"
      />

      <div className="relative z-10 flex flex-col gap-7">
        <div className="font-display text-5xl font-semibold leading-none tracking-tight">
          Advoxs<span className="text-auth-accent">.</span>
        </div>
        <p className="max-w-[34ch] text-lg leading-relaxed text-nav-ink-muted">
          Agentes de IA que atendem os clientes do seu escritório pelo WhatsApp — 24 horas por
          dia, com a linguagem da sua banca.
        </p>
      </div>

      <div className="relative z-10 hidden flex-col gap-5 lg:flex">
        {BENEFITS.map((benefit) => (
          <div key={benefit.title} className="flex items-start gap-3.5">
            <span className="mt-0.5 h-5 w-5 flex-none rounded-md border border-auth-accent/50 bg-auth-accent/20" />
            <div className="flex flex-col gap-0.5">
              <span className="text-[15px] font-semibold text-nav-ink">{benefit.title}</span>
              <span className="text-sm leading-relaxed text-nav-ink-muted">
                {benefit.description}
              </span>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}
