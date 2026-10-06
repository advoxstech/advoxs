import Link from "next/link";

import { PaymentForm } from "./PaymentForm";

export default function SignupPaymentPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-md">
        <h1 className="font-display text-3xl font-semibold text-ink">E-mail confirmado</h1>
        <p className="mt-3 text-sm text-muted">Agora você pode seguir para o pagamento. Sua conta será criada após a confirmação da compra.</p>
        <PaymentForm />
        <Link href="/" className="mt-6 block text-sm text-muted hover:underline">Voltar ao início</Link>
      </div>
    </main>
  );
}
