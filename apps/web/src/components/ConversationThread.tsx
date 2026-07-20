"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import { formatCredits, formatFullDateTime, formatMessageTime, formatPhone } from "@/lib/format";
import type { Conversation, Message } from "@/lib/types";

interface ConversationThreadProps {
  conversation: Conversation;
  onConversationUpdate: (conversation: Conversation) => void;
  onDeleted?: () => void;
  pollMs?: number;
}

export function ConversationThread({
  conversation,
  onConversationUpdate,
  onDeleted,
  pollMs = 4000,
}: ConversationThreadProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [messagesLoaded, setMessagesLoaded] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showTakeoverToast, setShowTakeoverToast] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const isManual = conversation.state === "human";

  const [summaryExpanded, setSummaryExpanded] = useState(() => Boolean(conversation.summary));
  const [summarizing, setSummarizing] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const generateSummary = async () => {
    setSummarizing(true);
    setSummaryError(null);
    try {
      const response = await backendFetch(`conversations/${conversation.id}/summary`, {
        method: "POST",
      });
      if (response.ok) {
        const updated: Conversation = await response.json();
        onConversationUpdate(updated);
        setSummaryExpanded(true);
      } else if (response.status === 402) {
        setSummaryError("Saldo de créditos esgotado — não é possível gerar o resumo.");
      } else {
        setSummaryError("Não foi possível gerar o resumo. Tente novamente.");
      }
    } catch {
      setSummaryError("Não foi possível gerar o resumo. Tente novamente.");
    } finally {
      setSummarizing(false);
    }
  };

  const loadMessages = useCallback(async () => {
    try {
      const response = await backendFetch(`conversations/${conversation.id}/messages`);
      if (response.ok) {
        const data: Message[] = await response.json();
        // A API devolve da mais recente para a mais antiga; o chat lê em ordem.
        setMessages(data.slice().reverse());
      }
    } catch {
      // rede indisponível: tenta no próximo ciclo
    } finally {
      setMessagesLoaded(true);
    }
  }, [conversation.id]);

  useEffect(() => {
    void loadMessages();
    if (!pollMs) {
      return;
    }
    const interval = setInterval(() => void loadMessages(), pollMs);
    return () => clearInterval(interval);
  }, [loadMessages, pollMs]);

  useEffect(() => {
    // Rola só a lista de mensagens — scrollIntoView rolaria TODOS os
    // ancestrais (inclusive os overflow-hidden do layout), deslocando a
    // página inteira sem como o usuário desfazer (visto em produção).
    const list = bottomRef.current?.parentElement;
    if (list) {
      list.scrollTop = list.scrollHeight;
    }
  }, [messages.length]);

  useEffect(() => {
    if (!pollMs || conversation.state !== "human") {
      return;
    }
    const sendHeartbeat = () =>
      void backendFetch(`conversations/${conversation.id}/heartbeat`, {
        method: "POST",
      }).catch(() => {
        // presença é best-effort; tenta no próximo ciclo
      });
    sendHeartbeat();
    const interval = setInterval(sendHeartbeat, pollMs);
    return () => clearInterval(interval);
  }, [conversation.id, conversation.state, pollMs]);

  const toggleState = async () => {
    setError(null);
    const response = await backendFetch(`conversations/${conversation.id}`, {
      method: "PATCH",
      body: JSON.stringify({ state: isManual ? "agent" : "human" }),
    });
    if (response.ok) {
      onConversationUpdate(await response.json());
    } else {
      setError("Não foi possível alterar o atendimento. Tente novamente.");
    }
  };

  const handleComposerFocus = async () => {
    if (isManual) {
      return;
    }
    setError(null);
    const response = await backendFetch(`conversations/${conversation.id}`, {
      method: "PATCH",
      body: JSON.stringify({ state: "human" }),
    });
    if (response.ok) {
      onConversationUpdate(await response.json());
      setShowTakeoverToast(true);
    } else {
      setError("Não foi possível assumir a conversa. Tente novamente.");
    }
  };

  const handleDelete = async () => {
    if (
      !window.confirm(
        "Apagar todo o histórico desta conversa? Essa ação não pode ser desfeita — as mensagens serão excluídas permanentemente.",
      )
    ) {
      return;
    }
    setError(null);
    const response = await backendFetch(`conversations/${conversation.id}`, {
      method: "DELETE",
    });
    if (response.ok) {
      onDeleted?.();
    } else {
      setError("Não foi possível excluir a conversa. Tente novamente.");
    }
  };

  const sendMessage = async (event: React.FormEvent) => {
    event.preventDefault();
    const content = draft.trim();
    if (!content || sending) {
      return;
    }
    setSending(true);
    setError(null);
    try {
      const response = await backendFetch(`conversations/${conversation.id}/messages`, {
        method: "POST",
        body: JSON.stringify({ content }),
      });
      if (response.ok) {
        const message: Message = await response.json();
        setMessages((prev) => [...prev, message]);
        setDraft("");
      } else if (response.status === 502) {
        setError("O WhatsApp não recebeu a mensagem. Tente novamente.");
      } else {
        setError("Não foi possível enviar. Tente novamente.");
      }
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center justify-between gap-4 border-b border-line bg-surface px-6 py-3.5">
        <div className="flex items-center gap-4">
          <h2 className="font-mono text-sm font-medium">
            {formatPhone(conversation.contact_phone_number)}
          </h2>
          {isManual ? (
            <span className="-rotate-2 select-none border-[3px] border-double border-brass px-2 py-0.5 font-mono text-[11px] font-medium uppercase tracking-[0.18em] text-brass">
              Atendimento manual
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs text-muted">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-accent" />
              agente respondendo
            </span>
          )}
          {conversation.end_customer_balance != null ? (
            <span className="font-mono text-xs text-muted">
              saldo do cliente: {formatCredits(conversation.end_customer_balance)} créditos
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-muted">IA respondendo</span>
            <button
              type="button"
              role="switch"
              aria-checked={!isManual}
              aria-label="IA respondendo"
              onClick={() => void toggleState()}
              className={`relative h-5 w-9 rounded-full transition-colors ${
                !isManual ? "bg-accent" : "bg-line"
              }`}
            >
              <span
                aria-hidden
                className={`absolute top-0.5 h-4 w-4 rounded-full bg-surface transition-transform ${
                  !isManual ? "translate-x-4" : "translate-x-0.5"
                }`}
              />
            </button>
          </div>
          <button
            type="button"
            onClick={() => void handleDelete()}
            className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
          >
            Excluir conversa
          </button>
        </div>
      </header>

      {showTakeoverToast ? (
        <div
          role="status"
          className="fixed right-6 top-20 z-50 w-72 rounded border border-brass bg-surface p-4 shadow-lg"
        >
          <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-brass">
            IA pausada
          </p>
          <p className="mt-1 text-sm leading-relaxed text-ink">
            Você assumiu esta conversa. A IA reassume após 3 minutos sem atividade.
          </p>
          <div className="mt-3 flex items-center gap-4">
            <button
              type="button"
              onClick={() => {
                setShowTakeoverToast(false);
                void toggleState();
              }}
              className="rounded-sm border border-line px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:border-accent hover:text-accent"
            >
              Devolver pra IA
            </button>
            <button
              type="button"
              onClick={() => setShowTakeoverToast(false)}
              className="text-xs text-muted transition-colors hover:text-ink"
            >
              Fechar
            </button>
          </div>
        </div>
      ) : null}

      <section className="border-b border-line bg-surface px-6 py-3">
        <button
          type="button"
          onClick={() => setSummaryExpanded((v) => !v)}
          className="flex w-full items-center justify-between text-left text-xs font-medium uppercase tracking-[0.14em] text-muted"
        >
          <span>Resumo da conversa</span>
          <span aria-hidden>{summaryExpanded ? "▾" : "▸"}</span>
        </button>
        {summaryExpanded ? (
          <div className="mt-2">
            {conversation.summary ? (
              <>
                <p className="text-sm leading-relaxed text-ink">{conversation.summary}</p>
                {conversation.summary_generated_at ? (
                  <p className="mt-1 text-xs text-muted">
                    Gerado em {formatFullDateTime(conversation.summary_generated_at)}
                  </p>
                ) : null}
              </>
            ) : (
              <p className="text-sm text-muted">Nenhum resumo gerado ainda.</p>
            )}
            {summaryError ? (
              <p role="alert" className="mt-2 text-xs text-danger">
                {summaryError}
                {summaryError.startsWith("Saldo") ? (
                  <>
                    {" "}
                    <a href="/creditos" className="underline">
                      Comprar créditos
                    </a>
                  </>
                ) : null}
              </p>
            ) : null}
            <button
              type="button"
              onClick={() => void generateSummary()}
              disabled={summarizing || (messagesLoaded && messages.length === 0)}
              className="mt-2 rounded-sm border border-line px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
            >
              {summarizing
                ? "Gerando…"
                : conversation.summary
                  ? "Atualizar resumo"
                  : "Resumir conversa"}
            </button>
          </div>
        ) : null}
      </section>

      <ul className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-6 py-5">
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
        <div ref={bottomRef} aria-hidden />
      </ul>

      <footer className="border-t border-line bg-surface px-6 py-4">
        {error ? (
          <p role="alert" className="mb-2 text-xs text-danger">
            {error}
          </p>
        ) : null}
        <form onSubmit={sendMessage} className="flex items-end gap-3">
          <input
            type="text"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={sending}
            placeholder="Escreva sua resposta…"
            aria-label="Resposta"
            onFocus={() => void handleComposerFocus()}
            className="flex-1 rounded-sm border border-line bg-ground px-3 py-2.5 text-sm placeholder:text-muted disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={!isManual || sending || !draft.trim()}
            className="rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink disabled:opacity-50"
          >
            {sending ? "Enviando…" : "Enviar"}
          </button>
        </form>
        {!isManual ? (
          <p className="mt-2 text-xs text-muted">
            Começar a digitar pausa a IA e você assume a conversa.
          </p>
        ) : null}
      </footer>
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const fromContact = message.sender_type === "contact";
  const fromHuman = message.sender_type === "human";

  return (
    <li className={`flex flex-col ${fromContact ? "items-start" : "items-end"}`}>
      <div
        className={`max-w-[72%] rounded-md px-3.5 py-2.5 text-sm leading-relaxed ${
          fromContact
            ? "border border-line bg-surface"
            : fromHuman
              ? "bg-brass-soft"
              : "bg-accent-soft"
        }`}
      >
        {!fromContact ? (
          <span
            className={`mb-0.5 block font-mono text-[10px] uppercase tracking-[0.14em] ${
              fromHuman ? "text-brass" : "text-accent"
            }`}
          >
            {fromHuman ? "Você" : "Agente"}
          </span>
        ) : null}
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
      </div>
      <div className="mt-1 flex items-center gap-1.5">
        {message.delivery_status === "failed" ? (
          <span className="rounded-sm bg-danger/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-danger">
            Não entregue
          </span>
        ) : null}
        <time className="font-mono text-[10px] text-muted">
          {formatMessageTime(message.created_at)}
        </time>
      </div>
    </li>
  );
}
