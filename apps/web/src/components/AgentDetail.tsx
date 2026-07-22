"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { backendFetch } from "@/lib/client-api";
import type { Agent } from "@/lib/types";

type AttachedFile = {
  id: string;
  filename: string;
  status: "processing" | "ready" | "error";
};

function extractErrorDetail(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export function AgentDetail({ agentId }: { agentId: string }) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      const [agentsResponse, attachedResponse] = await Promise.all([
        backendFetch("agents"),
        backendFetch(`agents/${agentId}/knowledge-base-files`),
      ]);
      if (agentsResponse.ok) {
        const agents: Agent[] = await agentsResponse.json();
        const found = agents.find((a) => a.id === agentId) ?? null;
        setAgent(found);
        if (found) {
          setName(found.name);
          setInstructions(found.instructions);
        }
      }
      if (attachedResponse.ok) {
        setAttachedFiles(await attachedResponse.json());
      }
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void load();
  }, [agentId]);

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setSaving(true);
    try {
      const response = await backendFetch(`agents/${agentId}`, {
        method: "PATCH",
        body: JSON.stringify({ name, instructions }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(body, "Falha ao salvar — tente novamente."));
        return;
      }
      setAgent(body);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setSaving(false);
    }
  }


  if (!loaded) {
    return (
      <main className="flex flex-1 items-center justify-center bg-ground text-sm text-muted">
        Carregando...
      </main>
    );
  }

  if (!agent) {
    return (
      <main className="flex flex-1 items-center justify-center bg-ground text-sm text-muted">
        Agente não encontrado.{" "}
        <Link href="/agentes" className="ml-1 text-accent hover:underline">
          Voltar
        </Link>
      </main>
    );
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      <header className="border-b border-line px-8 py-5">
        <Link href="/agentes" className="text-xs text-muted hover:text-ink">
          ← Agentes
        </Link>
        <h1 className="font-display text-xl font-semibold text-ink">{agent.name}</h1>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <form onSubmit={handleSave} className="flex max-w-md flex-col gap-4">
          <label className="flex flex-col gap-1 text-sm text-ink">
            Nome
            <input
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-ink">
            Instruções
            <textarea
              required
              rows={8}
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <button
            type="submit"
            disabled={saving}
            className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
          >
            {saving ? "Salvando..." : "Salvar"}
          </button>
        </form>

        <hr className="my-6 border-line" />

        <h2 className="font-display text-lg font-semibold text-ink">Base de conhecimento</h2>
        <p className="mt-4 max-w-md text-sm text-muted">
          {attachedFiles.length} arquivo{attachedFiles.length === 1 ? "" : "s"} anexado
          {attachedFiles.length === 1 ? "" : "s"} —{" "}
          <Link
            href={`/base-de-conhecimento?agent_id=${agent.id}`}
            className="text-accent hover:underline"
          >
            gerenciar na base de conhecimento
          </Link>
          .
        </p>
      </div>
    </main>
  );
}
