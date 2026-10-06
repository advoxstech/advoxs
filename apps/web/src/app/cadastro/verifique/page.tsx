import Link from "next/link";

import { ResendForm } from "./ResendForm";

export default function CheckEmailPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-md text-center">
        <h1 className="font-display text-3xl font-semibold text-ink">Verifique seu e-mail</h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          Enviamos um link de confirmação. Abra a mensagem e confirme seu endereço para liberar o pagamento. O link vale por 30 minutos.
        </p>
        <p className="mt-4 text-sm text-muted">Não recebeu? Confira a pasta de spam ou solicite outro envio após um minuto.</p>
        <ResendForm />
        <Link href="/" className="mt-6 inline-block font-semibold text-auth-accent-ink hover:underline">Voltar ao cadastro</Link>
      </div>
    </main>
  );
}
