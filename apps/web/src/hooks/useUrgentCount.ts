"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";

const URGENT_POLL_MS = 30000;

/** Contador de conversas urgentes, compartilhado pelo menu lateral e pela barra do celular. */
export function useUrgentCount(): number {
  const [count, setCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const response = await backendFetch("conversations/urgent-count");
        if (response.ok && !cancelled) {
          const body = await response.json();
          setCount(Number(body.count) || 0);
        }
      } catch {
        // Falha silenciosa: o contador é só um atalho visual.
      }
    }
    void load();
    const interval = setInterval(() => void load(), URGENT_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return count;
}
