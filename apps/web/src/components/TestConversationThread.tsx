"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import { formatMessageTime } from "@/lib/format";
import type { Conversation, Message } from "@/lib/types";

interface TestConversationThreadProps {
  conversation: Conversation;
  onDeleted: () => void;
  pollMs?: number;
}

const ACCEPTED_ATTACHMENT = ".pdf,.docx,.txt";

export function TestConversationThread({
  conversation,
  onDeleted,
  pollMs = 4000,
}: TestConversationThreadProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [attachment, setAttachment] = useState<File | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [grouped, setGrouped] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadMessages = useCallback(async () => {
    try {
      const response = await backendFetch(`conversations/${conversation.id}/messages`);
      if (response.ok) {
        const data: Message[] = await response.json();
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
    // Rola só a lista de mensagens — scrollIntoView rolaria TODOS os
    // ancestrais (inclusive os overflow-hidden do layout), deslocando a
    // página inteira sem como o usuário desfazer (visto em produção).
    const list = bottomRef.current?.parentElement;
    if (list) {
      list.scrollTop = list.scrollHeight;
    }
  }, [messages.length]);

  const sendMessage = async (event: React.FormEvent) => {
    event.preventDefault();
    const content = draft.trim();
    if ((!content && !attachment) || sending) {
      return;
    }
    setSending(true);
    setError(null);
    setGrouped(false);
    try {
      const form = new FormData();
      form.append("content", content);
      if (attachment) form.append("file", attachment);
      const response = await backendFetch(`conversations/${conversation.id}/test-messages`, {
        method: "POST",
        body: form,
      });
      if (response.ok) {
        const body: { messages: Message[]; grouped: boolean } = await response.json();
        // Dedupe por id: a chamada ao agente pode levar mais que um ciclo de
        // polling, e o poll já terá trazido a mensagem do contato (commitada
        // antes) — sem isso ela apareceria duplicada até o próximo poll.
        setMessages((prev) => {
          const seen = new Set(prev.map((m) => m.id));
          return [...prev, ...body.messages.filter((m) => !seen.has(m.id))];
        });
        setGrouped(body.grouped);
        setDraft("");
        setAttachment(null);
        if (fileInputRef.current) fileInputRef.current.value = "";
      } else if (response.status === 402) {
        setError("Saldo de créditos esgotado — compre créditos para testar os agentes.");
      } else {
        setError("Não foi possível falar com o agente. Tente novamente.");
        void loadMessages();
      }
    } catch {
      setError("Falha de conexão — tente novamente.");
    } finally {
      setSending(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm("Excluir esta conversa de teste? O histórico será apagado.")) {
      return;
    }
    const response = await backendFetch(`conversations/${conversation.id}`, {
      method: "DELETE",
    });
    if (response.ok) {
      onDeleted();
    } else {
      setError("Não foi possível excluir. Tente novamente.");
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center justify-between gap-4 border-b border-line bg-surface px-6 py-3.5">
        <div className="flex items-center gap-3">
          <h2 className="font-mono text-sm font-medium">Conversa de teste</h2>
          <span className="rounded-full bg-brass-soft px-3 py-1 font-mono text-[10px] uppercase tracking-[0.15em] text-brass">
            ambiente de teste
          </span>
          <span className="flex items-center gap-1.5 text-xs text-muted">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-accent" />
            {conversation.current_agent_name
              ? `${conversation.current_agent_name} respondendo`
              : "agente respondendo"}
          </span>
        </div>
        <button
          type="button"
          onClick={() => void handleDelete()}
          className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
        >
          Excluir conversa
        </button>
      </header>

      <ul className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-6 py-5">
        {messages.map((message) => (
          <TestMessageBubble key={message.id} message={message} />
        ))}
        {sending ? (
          <li className="flex items-start">
            <span className="rounded-md border border-line bg-surface px-3.5 py-2.5 text-sm text-muted">
              digitando…
            </span>
          </li>
        ) : null}
        <div ref={bottomRef} aria-hidden />
      </ul>

      <footer className="border-t border-line bg-surface px-6 py-4">
        {error ? (
          <p role="alert" className="mb-2 text-xs text-danger">
            {error}
            {error.startsWith("Saldo") ? (
              <>
                {" "}
                <a href="/creditos" className="underline">
                  Comprar créditos
                </a>
              </>
            ) : null}
          </p>
        ) : null}
        {grouped ? (
          <p className="mb-2 text-xs text-muted">
            Mensagem agrupada com a anterior — a resposta chega em instantes.
          </p>
        ) : null}
        {attachment ? (
          <p className="mb-2 flex items-center gap-2 text-xs text-muted">
            📎 {attachment.name}
            <button
              type="button"
              onClick={() => {
                setAttachment(null);
                if (fileInputRef.current) fileInputRef.current.value = "";
              }}
              className="text-danger hover:underline"
            >
              remover
            </button>
          </p>
        ) : null}
        <form onSubmit={sendMessage} className="flex items-end gap-3">
          <label
            className={`cursor-pointer rounded-sm border border-line bg-ground px-3 py-2.5 text-sm text-muted transition-colors hover:border-accent hover:text-accent ${sending ? "pointer-events-none opacity-60" : ""}`}
            title="Anexar arquivo (PDF, DOCX ou TXT)"
          >
            📎
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_ATTACHMENT}
              aria-label="Anexar arquivo à mensagem de teste"
              className="hidden"
              onChange={(event) => setAttachment(event.target.files?.[0] ?? null)}
            />
          </label>
          <input
            type="text"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={sending}
            placeholder="Escreva como se fosse o cliente…"
            aria-label="Mensagem de teste"
            className="flex-1 rounded-sm border border-line bg-ground px-3 py-2.5 text-sm placeholder:text-muted disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={sending || (!draft.trim() && !attachment)}
            className="rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink disabled:opacity-50"
          >
            {sending ? "Enviando…" : "Enviar"}
          </button>
        </form>
        <p className="mt-2 text-xs text-muted">
          Você escreve como o cliente; o agente responde de verdade (consome créditos). Anexe um
          PDF, DOCX ou TXT para testar a leitura de documentos.
        </p>
      </footer>
    </div>
  );
}

function TestMessageBubble({ message }: { message: Message }) {
  const fromContact = message.sender_type === "contact";

  return (
    <li className={`flex flex-col ${fromContact ? "items-end" : "items-start"}`}>
      <div
        className={`max-w-[72%] rounded-md px-3.5 py-2.5 text-sm leading-relaxed ${
          fromContact ? "bg-brass-soft" : "border border-line bg-surface"
        }`}
      >
        <span
          className={`mb-0.5 block font-mono text-[10px] uppercase tracking-[0.14em] ${
            fromContact ? "text-brass" : "text-accent"
          }`}
        >
          {fromContact ? "Você (cliente)" : "Agente"}
        </span>
        {message.media_url ? (
          <a
            href={message.media_url}
            target="_blank"
            rel="noopener noreferrer"
            className="break-words underline decoration-dotted underline-offset-2 hover:text-accent"
          >
            {message.content}
          </a>
        ) : (
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
        )}
      </div>
      <time className="mt-1 font-mono text-[10px] text-muted">
        {formatMessageTime(message.created_at)}
      </time>
    </li>
  );
}
