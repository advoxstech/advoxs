"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import { formatMessageTime, formatPhone } from "@/lib/format";
import type { Conversation, Message } from "@/lib/types";

interface ConversationThreadProps {
  conversation: Conversation;
  onConversationUpdate: (conversation: Conversation) => void;
  pollMs?: number;
}

export function ConversationThread({
  conversation,
  onConversationUpdate,
  pollMs = 4000,
}: ConversationThreadProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const isManual = conversation.state === "human";

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
    bottomRef.current?.scrollIntoView?.({ behavior: "auto", block: "end" });
  }, [messages.length]);

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
        </div>
        <button
          type="button"
          onClick={toggleState}
          className={`rounded-sm border px-3 py-1.5 text-xs font-medium transition-colors ${
            isManual
              ? "border-line text-muted hover:border-accent hover:text-accent"
              : "border-brass text-brass hover:bg-brass-soft"
          }`}
        >
          {isManual ? "Devolver ao agente" : "Assumir conversa"}
        </button>
      </header>

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
            disabled={!isManual || sending}
            placeholder={isManual ? "Escreva sua resposta…" : ""}
            aria-label="Resposta"
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
          <p className="mt-2 text-xs text-muted">Assuma a conversa para responder.</p>
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
      <time className="mt-1 font-mono text-[10px] text-muted">
        {formatMessageTime(message.created_at)}
      </time>
    </li>
  );
}
