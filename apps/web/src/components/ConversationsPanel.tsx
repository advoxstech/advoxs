"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type { Conversation } from "@/lib/types";

import { ConversationList } from "./ConversationList";
import { ConversationThread } from "./ConversationThread";
import { TestConversationThread } from "./TestConversationThread";

type Tab = "real" | "test";
const PAGE_SIZE = 50;

function mergeConversations(current: Conversation[], incoming: Conversation[]): Conversation[] {
  const incomingIds = new Set(incoming.map((conversation) => conversation.id));
  return [...incoming, ...current.filter((conversation) => !incomingIds.has(conversation.id))];
}

function appendConversations(current: Conversation[], incoming: Conversation[]): Conversation[] {
  const currentIds = new Set(current.map((conversation) => conversation.id));
  return [...current, ...incoming.filter((conversation) => !currentIds.has(conversation.id))];
}

export function ConversationsPanel({
  pollMs = 5000,
  initialOrigin = "real",
}: {
  pollMs?: number;
  initialOrigin?: "real" | "test";
}) {
  const [tab, setTab] = useState<Tab>(initialOrigin);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const refreshSequenceRef = useRef(0);
  const conversationsRef = useRef<Conversation[]>([]);

  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

  const loadConversations = useCallback(async () => {
    const sequence = ++refreshSequenceRef.current;
    setRefreshing(true);
    try {
      const response = await backendFetch(
        `conversations?origin=${tab}&limit=${PAGE_SIZE}&offset=0`,
      );
      if (!response.ok) throw new Error("conversations request failed");
      const data: Conversation[] = await response.json();
      if (sequence !== refreshSequenceRef.current) return;
      setConversations((current) => mergeConversations(current, data));
      if (conversationsRef.current.length <= PAGE_SIZE) {
        setHasMore(data.length === PAGE_SIZE);
      }
      setLoadError(false);
      setLastUpdatedAt(new Date());
    } catch {
      if (sequence === refreshSequenceRef.current) setLoadError(true);
    } finally {
      if (sequence === refreshSequenceRef.current) {
        setLoaded(true);
        setRefreshing(false);
      }
    }
  }, [tab]);

  const loadMore = async () => {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const response = await backendFetch(
        `conversations?origin=${tab}&limit=${PAGE_SIZE}&offset=${conversations.length}`,
      );
      if (!response.ok) throw new Error("older conversations request failed");
      const data: Conversation[] = await response.json();
      setConversations((current) => appendConversations(current, data));
      setHasMore(data.length === PAGE_SIZE);
      setLoadError(false);
      setLastUpdatedAt(new Date());
    } catch {
      setLoadError(true);
    } finally {
      setLoadingMore(false);
    }
  };

  useEffect(() => {
    setLoaded(false);
    void loadConversations();
    if (!pollMs) {
      return;
    }
    const interval = setInterval(() => void loadConversations(), pollMs);
    return () => clearInterval(interval);
  }, [loadConversations, pollMs, tab]);

  const selected = conversations.find((c) => c.id === selectedId) ?? null;

  const handleConversationUpdate = (updated: Conversation) => {
    setConversations((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
  };

  const switchTab = (next: Tab) => {
    if (next === tab) return;
    setTab(next);
    setSelectedId(null);
    setConversations([]);
    setLoaded(false);
    setHasMore(true);
    setLoadError(false);
    setLastUpdatedAt(null);
  };

  const createTestConversation = async () => {
    if (creating) return;
    setCreating(true);
    try {
      const response = await backendFetch("test-conversations", { method: "POST" });
      if (response.ok) {
        const created: Conversation = await response.json();
        setConversations((prev) => [created, ...prev]);
        setSelectedId(created.id);
      }
    } finally {
      setCreating(false);
    }
  };

  const handleDeleted = (id: string) => {
    setConversations((prev) => prev.filter((c) => c.id !== id));
    setSelectedId(null);
  };

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="flex items-baseline justify-between border-b border-line px-5 py-4">
        <h1 className="font-display text-xl font-semibold">Conversas</h1>
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => switchTab("real")}
            aria-pressed={tab === "real"}
            className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
              tab === "real" ? "bg-ink text-ground" : "text-muted hover:text-ink"
            }`}
          >
            Conversas
          </button>
          <button
            type="button"
            onClick={() => switchTab("test")}
            aria-pressed={tab === "test"}
            className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
              tab === "test" ? "bg-ink text-ground" : "text-muted hover:text-ink"
            }`}
          >
            Testes
          </button>
        </div>
      </header>

      {loadError ? (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 border-b border-brass/30 bg-brass-soft px-4 py-2 text-xs text-ink md:px-5"
        >
          <span>
            Não foi possível atualizar.
            {lastUpdatedAt
              ? ` Exibindo dados de ${lastUpdatedAt.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}.`
              : ""}
          </span>
          <button
            type="button"
            onClick={() => void loadConversations()}
            disabled={refreshing}
            className="shrink-0 font-medium text-accent disabled:opacity-50"
          >
            {refreshing ? "Atualizando…" : "Tentar novamente"}
          </button>
        </div>
      ) : null}

      <div className="flex min-h-0 min-w-0 flex-1">
        <aside
          className={`${selected ? "hidden md:flex" : "flex"} w-full shrink-0 flex-col border-r border-line md:w-80`}
        >
          <div className="flex items-center justify-end px-5 py-3">
            <span className="font-mono text-xs text-muted">{conversations.length}</span>
          </div>
          {tab === "test" ? (
            <button
              type="button"
              onClick={() => void createTestConversation()}
              disabled={creating}
              className="border-b border-line px-5 py-3 text-left text-sm font-medium text-accent transition-colors hover:bg-surface/60 disabled:opacity-50"
            >
              {creating ? "Criando…" : "Nova conversa de teste"}
            </button>
          ) : null}
          <ConversationList
            conversations={conversations}
            loaded={loaded}
            selectedId={selectedId}
            onSelect={setSelectedId}
            hasMore={hasMore}
            loadingMore={loadingMore}
            onLoadMore={() => void loadMore()}
            loadFailed={loadError}
          />
        </aside>

        <section
          className={`${selected ? "flex" : "hidden md:flex"} min-w-0 flex-1 flex-col bg-surface/40`}
        >
          {selected ? (
            selected.is_test ? (
              <TestConversationThread
                key={selected.id}
                conversation={selected}
                onDeleted={() => handleDeleted(selected.id)}
                onBack={() => setSelectedId(null)}
              />
            ) : (
              <ConversationThread
                key={selected.id}
                conversation={selected}
                onConversationUpdate={handleConversationUpdate}
                onDeleted={() => handleDeleted(selected.id)}
                onBack={() => setSelectedId(null)}
              />
            )
          ) : (
            <div className="flex flex-1 items-center justify-center p-8">
              <p className="max-w-xs text-center text-sm leading-relaxed text-muted">
                {tab === "test"
                  ? "Crie uma conversa de teste para experimentar os agentes sem WhatsApp."
                  : "Selecione uma conversa para acompanhar o atendimento."}
              </p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
