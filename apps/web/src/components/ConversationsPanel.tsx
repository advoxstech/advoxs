"use client";

import { useCallback, useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type { Conversation } from "@/lib/types";

import { ConversationList } from "./ConversationList";
import { ConversationsUsageReport } from "./ConversationsUsageReport";
import { ConversationThread } from "./ConversationThread";
import { TestConversationThread } from "./TestConversationThread";

type Tab = "real" | "test" | "usage";

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

  const loadConversations = useCallback(async () => {
    if (tab === "usage") return;
    try {
      const response = await backendFetch(`conversations?origin=${tab}`);
      if (response.ok) {
        setConversations(await response.json());
      }
    } catch {
      // rede indisponível: mantém a lista atual e tenta no próximo ciclo
    } finally {
      setLoaded(true);
    }
  }, [tab]);

  useEffect(() => {
    if (tab === "usage") return;
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
          <button
            type="button"
            onClick={() => switchTab("usage")}
            aria-pressed={tab === "usage"}
            className={`rounded-sm px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
              tab === "usage" ? "bg-ink text-ground" : "text-muted hover:text-ink"
            }`}
          >
            Consumo
          </button>
        </div>
      </header>

      {tab === "usage" ? (
        <ConversationsUsageReport />
      ) : (
        <div className="flex min-h-0 min-w-0 flex-1">
          <aside className="flex w-80 shrink-0 flex-col border-r border-line">
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
            />
          </aside>

          <section className="flex min-w-0 flex-1 flex-col bg-surface/40">
            {selected ? (
              selected.is_test ? (
                <TestConversationThread
                  key={selected.id}
                  conversation={selected}
                  onDeleted={() => handleDeleted(selected.id)}
                />
              ) : (
                <ConversationThread
                  key={selected.id}
                  conversation={selected}
                  onConversationUpdate={handleConversationUpdate}
                  onDeleted={() => handleDeleted(selected.id)}
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
      )}
    </div>
  );
}
