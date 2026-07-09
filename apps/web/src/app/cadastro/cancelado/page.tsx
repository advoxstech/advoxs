import Link from "next/link";

export default function CadastroCanceladoPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm text-center">
        <h1 className="font-display text-3xl font-semibold text-ink">Pagamento cancelado</h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          Nenhuma cobrança foi feita. Você pode tentar de novo quando quiser.
        </p>
        <Link
          href="/"
          className="mt-6 inline-block rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink"
        >
          Voltar
        </Link>
      </div>
    </main>
  );
}
