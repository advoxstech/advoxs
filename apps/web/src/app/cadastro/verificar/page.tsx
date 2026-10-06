import Link from "next/link";

import { VerifyForm } from "./VerifyForm";

export default async function VerifyEmailPage({ searchParams }: { searchParams: Promise<{ token?: string }> }) {
  const { token } = await searchParams;
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-md">
        <h1 className="font-display text-3xl font-semibold text-ink">Confirme seu e-mail</h1>
        <p className="mt-3 text-sm text-muted">Depois da confirmação, você poderá seguir para o pagamento.</p>
        {token ? <VerifyForm token={token} /> : <p className="mt-5 text-sm text-danger">Link inválido.</p>}
        <Link href="/" className="mt-6 block text-sm font-semibold text-auth-accent-ink hover:underline">Voltar ao cadastro</Link>
      </div>
    </main>
  );
}
