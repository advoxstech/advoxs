"use client";

import { useEffect, useRef, useState } from "react";

import { adminBackendFetch } from "@/lib/admin-client-api";

type Tenant = { id: string; name: string };

type ChatMessage = {
  id: string;
  role: "dev" | "agent" | "system";
  content: string;
  tokensUsed?: number | null;
};

type PlaygroundResponse = {
  responses: string[];
  tokens_used: number | null;
  current_agent: string | null;
  grouped: boolean;
};

const AGENT_LABELS: Record<string, string> = {
  agente_secretaria: "Secretária",
  agente_condominial: "Condominial",
  agente_contratos: "Contratos",
  agente_direito_consumidor: "Direito do Consumidor",
};

function agentLabel(currentAgent: string | null): string {
  if (!currentAgent) return "Secretária";
  return AGENT_LABELS[currentAgent] ?? currentAgent;
}

function newSessionId(): string {
  return crypto.randomUUID();
}

export function AdminPlaygroundPanel() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState<string>("");
  const [sessionId, setSessionId] = useState<string>(() => newSessionId());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [currentAgent, setCurrentAgent] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const previousSession = useRef<{ tenantId: string; sessionId: string } | null>(null);

  useEffect(() => {
    async function loadTenants() {
      const response = await adminBackendFetch("platform-admin/tenants");
      if (response.ok) {
        const data = (await response.json()) as Tenant[];
        setTenants(data);
        const first = data[0];
        if (first) {
          setTenantId((current) => current || first.id);
        }
      }
    }
    void loadTenants();
  }, []);

  function resetConversation(nextTenantId: string) {
    if (previousSession.current) {
      void adminBackendFetch(
        `platform-admin/playground/conversations/${previousSession.current.tenantId}/${previousSession.current.sessionId}`,
        { method: "DELETE" },
      );
    }
    previousSession.current = { tenantId: nextTenantId, sessionId };
    setSessionId(newSessionId());
    setMessages([]);
    setCurrentAgent(null);
  }

  function handleTenantChange(nextTenantId: string) {
    resetConversation(tenantId);
    setTenantId(nextTenantId);
  }

  function handleNewConversation() {
    resetConversation(tenantId);
  }

  async function handleSend() {
    const message = input.trim();
    if (!message || !tenantId || sending) return;

    const devMessage: ChatMessage = { id: crypto.randomUUID(), role: "dev", content: message };
    setMessages((prev) => [...prev, devMessage]);
    setInput("");
    setSending(true);

    try {
      const response = await adminBackendFetch("platform-admin/playground/messages", {
        method: "POST",
        body: JSON.stringify({ tenant_id: tenantId, session_id: sessionId, message }),
      });

      if (!response.ok) {
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "system",
            content: "Não foi possível falar com o agente agora.",
          },
        ]);
        return;
      }

      const data = (await response.json()) as PlaygroundResponse;

      if (data.grouped) {
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "system",
            content: "Mensagem agrupada à execução em andamento — aguarde a resposta.",
          },
        ]);
        return;
      }

      setCurrentAgent(data.current_agent);
      setMessages((prev) => [
        ...prev,
        ...data.responses.map((content, index) => ({
          id: crypto.randomUUID(),
          role: "agent" as const,
          content,
          tokensUsed: index === data.responses.length - 1 ? data.tokens_used : undefined,
        })),
      ]);
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "system",
          content: "Não foi possível falar com o agente agora.",
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex h-full flex-col p-8">
      <div className="flex items-center justify-between gap-4 border-b border-line pb-4">
        <div className="flex items-center gap-3">
          <label htmlFor="tenant-select" className="text-sm text-muted">
            Tenant
          </label>
          <select
            id="tenant-select"
            value={tenantId}
            onChange={(event) => handleTenantChange(event.target.value)}
            className="rounded-sm border border-line bg-surface px-3 py-1.5 text-sm"
          >
            {tenants.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>
                {tenant.name}
              </option>
            ))}
          </select>
          <span className="rounded-full bg-accent-soft px-3 py-1 font-mono text-[10px] uppercase tracking-[0.15em] text-accent">
            {agentLabel(currentAgent)}
          </span>
        </div>
        <button
          type="button"
          onClick={handleNewConversation}
          className="rounded-sm border border-line px-3 py-1.5 text-sm text-ink hover:bg-surface"
        >
          Nova conversa
        </button>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto py-4">
        {messages.map((message) => (
          <div
            key={message.id}
            className={
              message.role === "dev"
                ? "ml-auto max-w-md rounded-sm bg-accent px-4 py-2 text-sm text-surface"
                : message.role === "system"
                  ? "mx-auto max-w-md rounded-sm border border-line px-4 py-2 text-center text-sm text-muted"
                  : "max-w-md rounded-sm border border-line bg-surface px-4 py-2 text-sm text-ink"
            }
          >
            {message.content}
            {typeof message.tokensUsed === "number" && (
              <span className="ml-2 font-mono text-[10px] text-muted">
                {message.tokensUsed} tokens
              </span>
            )}
          </div>
        ))}
        {sending && <p className="text-sm text-muted">agente digitando...</p>}
      </div>

      <div className="flex gap-2 border-t border-line pt-4">
        <input
          type="text"
          value={input}
          placeholder="Digite uma mensagem..."
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void handleSend();
          }}
          className="flex-1 rounded-sm border border-line bg-surface px-3 py-2 text-sm"
        />
        <button
          type="button"
          onClick={() => void handleSend()}
          disabled={sending || !input.trim()}
          className="rounded-sm bg-accent px-4 py-2 text-sm font-medium text-surface disabled:opacity-60"
        >
          Enviar
        </button>
      </div>
    </div>
  );
}
