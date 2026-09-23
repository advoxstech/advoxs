"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type { Message } from "@/lib/types";

const PAGE_SIZE = 50;
const BOTTOM_THRESHOLD = 80;

function mergeMessages(current: Message[], incoming: Message[]): Message[] {
  const byId = new Map(current.map((message) => [message.id, message]));
  for (const message of incoming) byId.set(message.id, message);
  return [...byId.values()].sort((left, right) => {
    const dateDifference = Date.parse(left.created_at) - Date.parse(right.created_at);
    return dateDifference || left.id.localeCompare(right.id);
  });
}

export function usePaginatedMessages(conversationId: string, pollMs: number) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [hasOlder, setHasOlder] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const [newMessageCount, setNewMessageCount] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const listRef = useRef<HTMLUListElement>(null);
  const messagesRef = useRef<Message[]>([]);
  const nearBottomRef = useRef(true);
  const scrollToBottomRef = useRef(false);
  const preserveScrollHeightRef = useRef<number | null>(null);
  const refreshSequenceRef = useRef(0);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const refresh = useCallback(async () => {
    const sequence = ++refreshSequenceRef.current;
    setRefreshing(true);
    try {
      const response = await backendFetch(
        `conversations/${conversationId}/messages?limit=${PAGE_SIZE}&offset=0`,
      );
      if (!response.ok) throw new Error("messages request failed");
      const newestFirst: Message[] = await response.json();
      if (sequence !== refreshSequenceRef.current) return;
      const incoming = newestFirst.slice().reverse();
      const current = messagesRef.current;
      const knownIds = new Set(current.map((message) => message.id));
      const added = incoming.filter((message) => !knownIds.has(message.id)).length;
      if (current.length === 0 || nearBottomRef.current) {
        scrollToBottomRef.current = true;
        setNewMessageCount(0);
      } else if (added) {
        setNewMessageCount((count) => count + added);
      }
      setMessages((existing) => mergeMessages(existing, incoming));
      if (messagesRef.current.length === 0) setHasOlder(newestFirst.length === PAGE_SIZE);
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
  }, [conversationId]);

  useEffect(() => {
    void refresh();
    if (!pollMs) return;
    const interval = setInterval(() => void refresh(), pollMs);
    return () => clearInterval(interval);
  }, [pollMs, refresh]);

  const loadOlder = useCallback(async () => {
    if (loadingOlder || !hasOlder) return;
    setLoadingOlder(true);
    setLoadError(false);
    const list = listRef.current;
    preserveScrollHeightRef.current = list?.scrollHeight ?? null;
    try {
      const response = await backendFetch(
        `conversations/${conversationId}/messages?limit=${PAGE_SIZE}&offset=${messagesRef.current.length}`,
      );
      if (!response.ok) throw new Error("older messages request failed");
      const newestFirst: Message[] = await response.json();
      setMessages((current) => mergeMessages(current, newestFirst.slice().reverse()));
      setHasOlder(newestFirst.length === PAGE_SIZE);
      setLoadError(false);
      setLastUpdatedAt(new Date());
    } catch {
      preserveScrollHeightRef.current = null;
      setLoadError(true);
    } finally {
      setLoadingOlder(false);
    }
  }, [conversationId, hasOlder, loadingOlder]);

  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    if (preserveScrollHeightRef.current != null) {
      list.scrollTop += list.scrollHeight - preserveScrollHeightRef.current;
      preserveScrollHeightRef.current = null;
    } else if (scrollToBottomRef.current) {
      list.scrollTop = list.scrollHeight;
      scrollToBottomRef.current = false;
    }
  }, [messages.length]);

  const handleScroll = useCallback(() => {
    const list = listRef.current;
    if (!list) return;
    nearBottomRef.current = list.scrollHeight - list.scrollTop - list.clientHeight <= BOTTOM_THRESHOLD;
    if (nearBottomRef.current) setNewMessageCount(0);
  }, []);

  const scrollToLatest = useCallback(() => {
    const list = listRef.current;
    if (!list) return;
    list.scrollTop = list.scrollHeight;
    nearBottomRef.current = true;
    setNewMessageCount(0);
  }, []);

  const appendMessages = useCallback((incoming: Message | Message[]) => {
    scrollToBottomRef.current = true;
    nearBottomRef.current = true;
    setNewMessageCount(0);
    setMessages((current) => mergeMessages(current, Array.isArray(incoming) ? incoming : [incoming]));
  }, []);

  return {
    messages,
    loaded,
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
  };
}
