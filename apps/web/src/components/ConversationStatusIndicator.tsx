import type { Conversation, ConversationStatus } from "@/lib/types";

const styles: Record<ConversationStatus, { text: string; dot: string }> = {
  agent: { text: "text-muted", dot: "bg-accent" },
  processing: { text: "text-accent", dot: "bg-accent" },
  human: { text: "text-brass", dot: "bg-brass" },
  billing_gate: { text: "text-brass", dot: "bg-brass" },
  failed: { text: "text-danger", dot: "bg-danger" },
};

export function conversationStatusLabel(conversation: Conversation): string {
  const status = conversation.status ?? conversation.state;
  if (status === "processing") {
    return conversation.current_agent_name
      ? `${conversation.current_agent_name} processando`
      : "IA processando";
  }
  if (status === "human") return "Atendimento humano";
  if (status === "billing_gate") return "Aguardando pagamento";
  if (status === "failed") return "Falha no atendimento";
  return conversation.current_agent_name
    ? `${conversation.current_agent_name} disponível`
    : "IA disponível";
}

export function ConversationStatusIndicator({ conversation }: { conversation: Conversation }) {
  const status = conversation.status ?? conversation.state;
  const style = styles[status];
  return (
    <span className={`flex items-center gap-1.5 text-xs ${style.text}`}>
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
      {conversationStatusLabel(conversation)}
    </span>
  );
}
