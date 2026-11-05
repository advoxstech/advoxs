import type { MessageMedia } from "@/lib/types";

const REASONS: Record<string, string> = {
  download_failed: "não foi possível baixar o arquivo",
  timeout: "o serviço demorou demais",
  provider_unavailable: "serviço de transcrição indisponível",
  invalid_format: "formato não suportado",
  too_large: "arquivo maior que 20 MB",
  too_long: "passa do limite de duração",
  silence: "sem fala detectada",
  no_credits: "sem créditos",
  billing_gate: "aguardando pagamento do cliente",
};

function highlight(text: string) {
  return text.split(/(\[\?.*?\?\])/g).map((part, i) =>
    part.startsWith("[?") && part.endsWith("?]") ? (
      <mark key={i} title="Trecho incerto" className="rounded-sm bg-brass-soft px-0.5">
        {part.slice(2, -2)}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

export function MediaTranscript({ media }: { media: MessageMedia }) {
  if (media.kind !== "audio") return null;
  if (media.status === "done" && media.text) {
    return (
      <div className="mt-2 border-t border-line pt-2">
        <span className="block font-mono text-micro uppercase tracking-[0.14em] text-muted">
          Transcrição automática
        </span>
        <p className="whitespace-pre-wrap break-words">{highlight(media.text)}</p>
      </div>
    );
  }
  if (media.status === "pending" || media.status === "processing") {
    return <p className="mt-2 text-xs text-muted">Transcrevendo áudio…</p>;
  }
  const reason = REASONS[media.error_code ?? ""] ?? "erro no processamento";
  return <p className="mt-2 text-xs text-muted">Áudio não transcrito: {reason}</p>;
}
