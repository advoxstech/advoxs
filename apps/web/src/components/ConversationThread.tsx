"use client";

import { useState } from "react";

import { usePaginatedMessages } from "@/hooks/usePaginatedMessages";
import { backendFetch } from "@/lib/client-api";
import { formatCredits, formatFullDateTime, formatMessageTime, formatPhone } from "@/lib/format";
import type { Conversation, Message } from "@/lib/types";

import { ConversationStatusIndicator } from "./ConversationStatusIndicator";

function formatUpdateTime(date: Date): string {
  return date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

interface ConversationThreadProps {
  conversation: Conversation;
  onConversationUpdate: (conversation: Conversation) => void;
  onDeleted?: () => void;
  onBack?: () => void;
  pollMs?: number;
}

export function ConversationThread({
  conversation,
  onConversationUpdate,
  onDeleted,
  onBack,
  pollMs = 4000,
}: ConversationThreadProps) {
  const {
    messages,
    loaded: messagesLoaded,
    loadingOlder,
    hasOlder,
    loadError,
    lastUpdatedAt,
    newMessageCount,
    refreshing,
    listRef,
    refresh,
    loadOlder,
    handleScroll,
    scrollToLatest,
    appendMessages,
  } = usePaginatedMessages(conversation.id, pollMs);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isManual = conversation.state === "human";

  const [exemptionError, setExemptionError] = useState<string | null>(null);

  const toggleBillingExemption = async () => {
    const goingExempt = !conversation.end_customer_billing_exempt;
    const confirmed = goingExempt
      ? window.confirm(
          "Isentar este cliente de cobrança? Ele poderá conversar livremente e receberá um aviso de que a conversa passou a ser gratuita.",
        )
      : window.confirm(
          "A partir da próxima mensagem, esse cliente volta a ser cobrado normalmente. Confirmar?",
        );
    if (!confirmed) {
      return;
    }
    setExemptionError(null);
    const response = await backendFetch(`conversations/${conversation.id}/billing-exemption`, {
      method: "PATCH",
      body: JSON.stringify({ exempt: goingExempt }),
    });
    if (response.ok) {
      onConversationUpdate(await response.json());
    } else {
      setExemptionError("Não foi possível alterar a cobrança deste cliente. Tente novamente.");
    }
  };

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
        appendMessages(message);
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
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-surface px-4 py-3.5 md:px-6">
        <div className="flex min-w-0 items-center gap-3 md:gap-4">
          {onBack ? (
            <button
              type="button"
              onClick={onBack}
              className="shrink-0 rounded-sm border border-line px-2.5 py-1.5 text-xs font-medium text-ink md:hidden"
            >
              Voltar
            </button>
          ) : null}
          <h2 className="font-mono text-sm font-medium">
            {formatPhone(conversation.contact_phone_number)}
          </h2>
          <ConversationStatusIndicator conversation={conversation} />
          {conversation.end_customer_balance != null ? (
            <span className="font-mono text-xs text-muted">
              saldo do cliente: {formatCredits(conversation.end_customer_balance)} créditos
            </span>
          ) : null}
          {conversation.end_customer_cycle_total != null ? (
            <span className="font-mono text-xs text-muted">
              {formatCredits(conversation.end_customer_cycle_consumed ?? 0)} de{" "}
              {formatCredits(conversation.end_customer_cycle_total)} créditos usados
            </span>
          ) : null}
        </div>
        <div className="flex w-full flex-wrap items-center justify-end gap-3 md:w-auto md:gap-4">
          {conversation.end_customer_billing_enabled ? (
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-muted">Cobrança gratuita</span>
              <button
                type="button"
                role="switch"
                aria-checked={conversation.end_customer_billing_exempt}
                aria-label="Cobrança gratuita"
                onClick={() => void toggleBillingExemption()}
                className={`relative h-5 w-9 rounded-full transition-colors ${
                  conversation.end_customer_billing_exempt ? "bg-accent" : "bg-line"
                }`}
              >
                <span
                  aria-hidden
                  className={`absolute top-0.5 h-4 w-4 rounded-full bg-surface transition-transform ${
                    conversation.end_customer_billing_exempt
                      ? "translate-x-4"
                      : "translate-x-0.5"
                  }`}
                />
              </button>
            </div>
          ) : null}
          <button
            type="button"
            onClick={() => void toggleState()}
            className="rounded-sm border border-line px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:border-accent hover:text-accent"
          >
            {isManual ? "Devolver para IA" : "Assumir atendimento"}
          </button>
          <button
            type="button"
            onClick={() => void handleDelete()}
            className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
          >
            Excluir conversa
          </button>
        </div>
      </header>
      {exemptionError ? (
        <p role="alert" className="border-b border-line bg-surface px-6 py-2 text-xs text-danger">
          {exemptionError}
        </p>
      ) : null}

      {loadError ? (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 border-b border-brass/30 bg-brass-soft px-4 py-2 text-xs text-ink md:px-6"
        >
          <span>
            Não foi possível atualizar.
            {lastUpdatedAt ? ` Exibindo dados de ${formatUpdateTime(lastUpdatedAt)}.` : ""}
          </span>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={refreshing}
            className="font-medium text-accent disabled:opacity-50"
          >
            {refreshing ? "Atualizando…" : "Tentar novamente"}
          </button>
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

      <ul
        ref={listRef}
        onScroll={handleScroll}
        className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-5 md:px-6"
      >
        {!messagesLoaded ? (
          <li className="py-4 text-center text-sm text-muted">Carregando mensagens…</li>
        ) : null}
        {hasOlder && messagesLoaded && messages.length > 0 ? (
          <li className="flex justify-center pb-2">
            <button
              type="button"
              onClick={() => void loadOlder()}
              disabled={loadingOlder}
              className="rounded-sm border border-line px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
            >
              {loadingOlder ? "Carregando…" : "Carregar mensagens anteriores"}
            </button>
          </li>
        ) : null}
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
      </ul>

      {newMessageCount > 0 ? (
        <button
          type="button"
          onClick={scrollToLatest}
          className="mx-auto mb-3 rounded-full bg-ink px-4 py-2 text-xs font-medium text-ground shadow-lg"
        >
          {newMessageCount} {newMessageCount === 1 ? "mensagem nova" : "mensagens novas"}
        </button>
      ) : null}

      <footer className="border-t border-line bg-surface px-4 py-4 md:px-6">
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
            placeholder={isManual ? "Escreva sua resposta…" : "Assuma o atendimento para responder"}
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
          <p className="mt-2 text-xs text-muted">Assuma o atendimento para responder manualmente.</p>
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
      <div className="mt-1 flex items-center gap-1.5">
        {message.delivery_status === "failed" ? (
          <span className="rounded-sm bg-danger/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-danger">
            Não entregue
          </span>
        ) : message.delivery_status === "cancelled" ? (
          <span className="rounded-sm bg-muted/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-muted">
            Cancelado
          </span>
        ) : message.delivery_status === "pending" ? (
          <span className="rounded-sm bg-brass-soft px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-brass">
            Enviando
          </span>
        ) : null}
        <time className="font-mono text-[10px] text-muted">
          {formatMessageTime(message.created_at)}
        </time>
      </div>
    </li>
  );
}
