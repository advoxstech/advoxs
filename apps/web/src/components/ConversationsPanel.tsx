"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useTeamSession } from "./TeamSession";
import { backendFetch } from "@/lib/client-api";
import type { Conversation, ContactTag } from "@/lib/types";
import { ContactsPanel } from "./ContactsPanel";

import { ConversationList } from "./ConversationList";
import { ConversationThread } from "./ConversationThread";
import { TestConversationThread } from "./TestConversationThread";

type Tab = "real" | "test";
const PAGE_SIZE = 50;

function mergeConversations(
  current: Conversation[],
  incoming: Conversation[],
): Conversation[] {
  const incomingIds = new Set(incoming.map((conversation) => conversation.id));
  return [
    ...incoming,
    ...current.filter((conversation) => !incomingIds.has(conversation.id)),
  ];
}

function appendConversations(
  current: Conversation[],
  incoming: Conversation[],
): Conversation[] {
  const currentIds = new Set(current.map((conversation) => conversation.id));
  return [
    ...current,
    ...incoming.filter((conversation) => !currentIds.has(conversation.id)),
  ];
}

export function ConversationsPanel({
  pollMs = 5000,
  initialOrigin = "real",
  initialUrgentOnly = false,
  initialConversationId,
}: {
  pollMs?: number;
  initialOrigin?: "real" | "test";
  initialUrgentOnly?: boolean;
  initialConversationId?: string;
}) {
  const identity = useTeamSession();
  const isLawyer = identity?.role === "lawyer";
  const [contactsOpen, setContactsOpen] = useState(false);
  const [contactSeed, setContactSeed] = useState<Conversation | null>(null);
  const [tagFilter, setTagFilter] = useState("");
  const [tags, setTags] = useState<ContactTag[]>([]);
  const [tagsRevision, setTagsRevision] = useState(0);
  const [tagLoadError, setTagLoadError] = useState(false);
  useEffect(() => {
    let active = true;
    void backendFetch("contact-tags")
      .then(async (response) => {
        if (!response.ok) throw new Error("tags request failed");
        const labels: ContactTag[] = await response.json();
        if (active) {
          setTags(labels);
          setTagLoadError(false);
          setTagFilter((current) =>
            current &&
            current !== "untagged" &&
            !labels.some((t) => t.id === current)
              ? ""
              : current,
          );
        }
      })
      .catch(() => {
        if (active) setTagLoadError(true);
      });
    return () => {
      active = false;
    };
  }, [tagsRevision]);
  const tagQuery =
    tagFilter === "untagged"
      ? "&untagged=true"
      : tagFilter
        ? `&tag_id=${encodeURIComponent(tagFilter)}`
        : "";
  const [mine, setMine] = useState(false);
  const [tab, setTab] = useState<Tab>(isLawyer ? "real" : initialOrigin);
  const [urgentOnly, setUrgentOnly] = useState(initialUrgentOnly);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(
    initialConversationId ?? null,
  );
  const [creating, setCreating] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const refreshSequenceRef = useRef(0);
  const conversationsRef = useRef<Conversation[]>([]);
  const selectedIdRef = useRef(selectedId);
  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

  const loadConversations = useCallback(async () => {
    const sequence = ++refreshSequenceRef.current;
    setRefreshing(true);
    try {
      const pages = tagFilter
        ? Math.max(1, Math.ceil(conversationsRef.current.length / PAGE_SIZE))
        : 1;
      const chunks = await Promise.all(
        Array.from({ length: pages }, async (_, page) => {
          const response = await backendFetch(
            `conversations?origin=${tab}${urgentOnly ? "&urgent=true" : ""}${mine ? "&mine=true" : ""}${tagQuery}&limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`,
          );
          if (!response.ok) throw new Error("conversations request failed");
          return (await response.json()) as Conversation[];
        }),
      );
      const data = chunks.flat();
      if (sequence !== refreshSequenceRef.current) return;
      const selectedId = selectedIdRef.current;
      if (
        selectedId &&
        !mine &&
        !urgentOnly &&
        !tagFilter &&
        !data.some((c) => c.id === selectedId)
      ) {
        const detail = await backendFetch(`conversations/${selectedId}/detail`);
        if (detail.ok) {
          const item = await detail.json();
          if (!data.some((c) => c.id === item.id)) data.push(item);
        } else if (detail.status === 404 || detail.status === 403) {
          setSelectedId(null);
          setConversations((current) =>
            current.filter((c) => c.id !== selectedId),
          );
        }
      }
      if (sequence !== refreshSequenceRef.current) return;
      // Com o filtro ativo, uma conversa resolvida precisa sair da lista.
      setConversations((current) =>
        urgentOnly || mine || isLawyer || tagFilter
          ? data
          : mergeConversations(current, data),
      );
      if (tagFilter || conversationsRef.current.length <= PAGE_SIZE) {
        setHasMore(chunks.at(-1)?.length === PAGE_SIZE);
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
  }, [tab, urgentOnly, mine, isLawyer, tagFilter, tagQuery]);

  const loadMore = async () => {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    const sequence = refreshSequenceRef.current;
    try {
      const response = await backendFetch(
        `conversations?origin=${tab}${urgentOnly ? "&urgent=true" : ""}${mine ? "&mine=true" : ""}${tagQuery}&limit=${PAGE_SIZE}&offset=${conversations.length}`,
      );
      if (!response.ok) throw new Error("older conversations request failed");
      const data: Conversation[] = await response.json();
      if (sequence !== refreshSequenceRef.current) return;
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
    void loadConversations();
    if (!pollMs) {
      return;
    }
    const interval = setInterval(() => void loadConversations(), pollMs);
    return () => clearInterval(interval);
  }, [loadConversations, pollMs, tab]);

  const selected = conversations.find((c) => c.id === selectedId) ?? null;

  const handleConversationUpdate = (updated: Conversation) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === updated.id ? updated : c)),
    );
  };

  const toggleUrgentOnly = () => {
    setUrgentOnly((value) => !value);
    setSelectedId(null);
    setConversations([]);
    setLoaded(false);
    setHasMore(true);
    setLoadError(false);
    setLastUpdatedAt(null);
    setActionNotice(null);
  };

  const switchTab = (next: Tab) => {
    if (next === tab) return;
    setTab(next);
    setTagFilter("");
    setSelectedId(null);
    setConversations([]);
    setLoaded(false);
    setHasMore(true);
    setLoadError(false);
    setLastUpdatedAt(null);
    setActionNotice(null);
  };

  const createTestConversation = async () => {
    if (creating) return;
    setCreating(true);
    try {
      const response = await backendFetch("test-conversations", {
        method: "POST",
      });
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
    refreshSequenceRef.current += 1;
    selectedIdRef.current = null;
    setRefreshing(false);
    setConversations((prev) => prev.filter((c) => c.id !== id));
    setSelectedId(null);
    setActionNotice(
      "Exclusão iniciada. O histórico será removido com segurança em segundo plano.",
    );
  };

  if (contactsOpen && !isLawyer)
    return (
      <ContactsPanel
        initialConversation={contactSeed}
        onClose={() => {
          setContactsOpen(false);
          void loadConversations();
        }}
        onChanged={() => {
          setTagsRevision((v) => v + 1);
          void loadConversations();
        }}
      />
    );

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex items-center gap-3 border-b border-line px-5 py-2 text-sm">
        <label>
          <input
            type="checkbox"
            checked={mine}
            onChange={(e) => {
              setMine(e.target.checked);
              setConversations([]);
              setSelectedId(null);
              setHasMore(true);
            }}
          />{" "}
          Meus atendimentos
        </label>
      </div>
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line px-5 py-4">
        <h1 className="font-display text-[26px] leading-tight font-semibold">
          Conversas
        </h1>
        {!isLawyer && (
          <button
            className="text-sm text-accent underline"
            onClick={() => {
              setContactSeed(null);
              setContactsOpen(true);
            }}
          >
            Contatos e tags
          </button>
        )}
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => switchTab("real")}
            aria-pressed={tab === "real"}
            className={`rounded-sm px-3 py-1 font-mono text-micro uppercase tracking-[0.14em] transition-colors ${
              tab === "real"
                ? "bg-ink text-ground"
                : "text-muted hover:text-ink"
            }`}
          >
            Conversas
          </button>
          {!isLawyer && (
            <button
              type="button"
              onClick={() => switchTab("test")}
              aria-pressed={tab === "test"}
              className={`rounded-sm px-3 py-1 font-mono text-micro uppercase tracking-[0.14em] transition-colors ${
                tab === "test"
                  ? "bg-ink text-ground"
                  : "text-muted hover:text-ink"
              }`}
            >
              Testes
            </button>
          )}
        </div>
      </header>
      {tagLoadError && (
        <p role="alert" className="px-5 py-2 text-sm text-danger">
          Não foi possível carregar as tags.{" "}
          <button
            className="underline"
            onClick={() => setTagsRevision((v) => v + 1)}
          >
            Recarregar tags
          </button>
        </p>
      )}
      {actionNotice ? (
        <p
          role="status"
          className="border-b border-line bg-brass-soft px-5 py-2 text-sm text-ink"
        >
          {actionNotice}
        </p>
      ) : null}

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
          {tab === "real" && (
            <label className="border-b border-line px-5 py-3 text-sm">
              Filtrar por tag
              <select
                aria-label="Filtrar por tag"
                className="mt-1 block w-full rounded border border-line bg-surface p-2"
                value={tagFilter}
                onChange={(e) => {
                  refreshSequenceRef.current += 1;
                  conversationsRef.current = [];
                  setTagFilter(e.target.value);
                  setSelectedId(null);
                  setConversations([]);
                  setLoaded(false);
                  setHasMore(true);
                }}
              >
                <option value="">Todas</option>
                <option value="untagged">Sem tag</option>
                {tags.map((tag) => (
                  <option key={tag.id} value={tag.id}>
                    {tag.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="flex items-center justify-between px-5 py-3">
            <button
              type="button"
              onClick={toggleUrgentOnly}
              aria-pressed={urgentOnly}
              className={`rounded-sm border px-2.5 py-1 font-mono text-micro uppercase tracking-[0.14em] transition-colors ${
                urgentOnly
                  ? "border-danger bg-danger text-white"
                  : "border-line text-muted hover:border-danger hover:text-danger"
              }`}
            >
              Urgentes
            </button>
            <span className="font-mono text-xs text-muted">
              {conversations.length}
            </span>
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
                onConversationUpdate={handleConversationUpdate}
              />
            ) : (
              <ConversationThread
                onEditContact={() => {
                  setContactSeed(selected);
                  setContactsOpen(true);
                }}
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
