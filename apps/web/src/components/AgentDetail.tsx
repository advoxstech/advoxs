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
  const [allFiles, setAllFiles] = useState<AttachedFile[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [saving, setSaving] = useState(false);
  const [selectedFileId, setSelectedFileId] = useState("");
  const [attaching, setAttaching] = useState(false);

  async function load() {
    try {
      const [agentsResponse, attachedResponse, allFilesResponse] = await Promise.all([
        backendFetch("agents"),
        backendFetch(`agents/${agentId}/knowledge-base-files`),
        backendFetch("knowledge-base/files"),
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
      if (allFilesResponse.ok) {
        setAllFiles(await allFilesResponse.json());
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

  async function handleAttach(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedFileId) return;
    setFeedback(null);
    setAttaching(true);
    try {
      const response = await backendFetch(`agents/${agentId}/knowledge-base-files`, {
        method: "POST",
        body: JSON.stringify({ knowledge_base_file_id: selectedFileId }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(extractErrorDetail(body, "Falha ao anexar — tente novamente."));
        return;
      }
      setSelectedFileId("");
      await load();
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setAttaching(false);
    }
  }

  async function handleDetach(file: AttachedFile) {
    if (!window.confirm(`Desanexar "${file.filename}" deste agente?`)) return;
    try {
      const response = await backendFetch(`agents/${agentId}/knowledge-base-files/${file.id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(extractErrorDetail(body, "Falha ao desanexar — tente novamente."));
        return;
      }
      setAttachedFiles(attachedFiles.filter((f) => f.id !== file.id));
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
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

  const attachedIds = new Set(attachedFiles.map((f) => f.id));
  const availableFiles = allFiles.filter((f) => !attachedIds.has(f.id));

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
        <ul className="mt-4 max-w-md">
          {attachedFiles.length === 0 && (
            <li className="py-4 text-sm text-muted">Nenhum arquivo anexado ainda.</li>
          )}
          {attachedFiles.map((file) => (
            <li
              key={file.id}
              className="flex items-center justify-between border-b border-line py-3"
            >
              <p className="truncate text-ink">{file.filename}</p>
              <button
                type="button"
                onClick={() => void handleDetach(file)}
                className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
              >
                Desanexar
              </button>
            </li>
          ))}
        </ul>

        <form onSubmit={handleAttach} className="mt-4 flex max-w-md items-end gap-2">
          <label className="flex flex-1 flex-col gap-1 text-sm text-ink">
            Anexar arquivo já enviado
            <select
              value={selectedFileId}
              onChange={(event) => setSelectedFileId(event.target.value)}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            >
              <option value="">Selecione um arquivo</option>
              {availableFiles.map((file) => (
                <option key={file.id} value={file.id}>
                  {file.filename}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            disabled={attaching || !selectedFileId}
            className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
          >
            {attaching ? "Anexando..." : "Anexar"}
          </button>
        </form>

        <p className="mt-4 text-sm text-muted">
          Ou{" "}
          <Link
            href={`/base-de-conhecimento?agent_id=${agent.id}`}
            className="text-accent hover:underline"
          >
            envie um arquivo novo direto pra este agente
          </Link>
          .
        </p>
      </div>
    </main>
  );
}
