export default function PagamentoConfirmadoPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm text-center">
        <h1 className="font-display text-3xl font-semibold text-ink">Pagamento recebido</h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          Sua compra foi confirmada. A confirmação e instruções serão enviadas em breve via WhatsApp.
        </p>
        <p className="mt-4 text-sm text-muted">
          Você pode fechar esta aba e voltar para a conversa.
        </p>
      </div>
    </main>
  );
}
