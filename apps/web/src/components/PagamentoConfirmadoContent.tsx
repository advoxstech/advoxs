const CONTENT = {
  sucesso: {
    title: "Pagamento recebido",
    body: "Sua compra foi confirmada. A confirmação e instruções serão enviadas em breve via WhatsApp.",
  },
  cancelado: {
    title: "Pagamento cancelado",
    body: "Nenhum valor foi cobrado. Se quiser, você pode voltar pra conversa no WhatsApp e tentar de novo quando quiser.",
  },
} as const;

export function PagamentoConfirmadoContent({ status }: { status?: string }) {
  const { title, body } = status === "cancelado" ? CONTENT.cancelado : CONTENT.sucesso;

  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm text-center">
        <h1 className="font-display text-3xl font-semibold text-ink">{title}</h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">{body}</p>
        <p className="mt-4 text-sm text-muted">
          Você pode fechar esta aba e voltar para a conversa.
        </p>
      </div>
    </main>
  );
}
