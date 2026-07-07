"use client";

import { useCallback, useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type { Conversation } from "@/lib/types";

import { ConversationList } from "./ConversationList";
import { ConversationThread } from "./ConversationThread";

export function ConversationsPanel({ pollMs = 5000 }: { pollMs?: number }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const loadConversations = useCallback(async () => {
    try {
      const response = await backendFetch("conversations");
      if (response.ok) {
        setConversations(await response.json());
      }
    } catch {
      // rede indisponível: mantém a lista atual e tenta no próximo ciclo
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void loadConversations();
    if (!pollMs) {
      return;
    }
    const interval = setInterval(() => void loadConversations(), pollMs);
    return () => clearInterval(interval);
  }, [loadConversations, pollMs]);

  const selected = conversations.find((c) => c.id === selectedId) ?? null;

  const handleConversationUpdate = (updated: Conversation) => {
    setConversations((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
  };

  return (
    <div className="flex min-w-0 flex-1">
      <aside className="flex w-80 shrink-0 flex-col border-r border-line">
        <header className="flex items-baseline justify-between border-b border-line px-5 py-4">
          <h1 className="font-display text-xl font-semibold">Conversas</h1>
          <span className="font-mono text-xs text-muted">{conversations.length}</span>
        </header>
        <ConversationList
          conversations={conversations}
          loaded={loaded}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
      </aside>

      <section className="flex min-w-0 flex-1 flex-col bg-surface/40">
        {selected ? (
          <ConversationThread
            key={selected.id}
            conversation={selected}
            onConversationUpdate={handleConversationUpdate}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center p-8">
            <p className="max-w-xs text-center text-sm leading-relaxed text-muted">
              Selecione uma conversa para acompanhar o atendimento.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
