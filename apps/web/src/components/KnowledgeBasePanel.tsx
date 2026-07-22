"use client";

import { useCallback, useEffect, useState } from "react";

import { AgentFolder } from "@/components/AgentFolder";
import type { KbFile } from "@/components/AgentFolder";
import { backendFetch } from "@/lib/client-api";
import type { Agent } from "@/lib/types";

const MAX_FILE_BYTES = 20 * 1024 * 1024;

export function KnowledgeBasePanel({ pollMs = 5000 }: { pollMs?: number }) {
  const [files, setFiles] = useState<KbFile[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [focusedAgentId, setFocusedAgentId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await backendFetch("knowledge-base/files");
      if (response.ok) {
        setFiles(await response.json());
      }
    } catch {
      // rede indisponível: mantém a lista atual e tenta no próximo ciclo
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    async function loadAgents() {
      try {
        const response = await backendFetch("agents");
        if (!response.ok) return;
        const body: Agent[] = await response.json();
        setAgents(body);

        const fromUrl = new URLSearchParams(window.location.search).get("agent_id");
        if (fromUrl && body.some((a) => a.id === fromUrl)) {
          setFocusedAgentId(fromUrl);
        }
      } catch {
        // fail-safe: sem agentes carregados, nenhuma pasta é exibida
      }
    }
    void loadAgents();
  }, []);

  const hasProcessing = files.some((file) => file.status === "processing");

  useEffect(() => {
    if (!pollMs || !hasProcessing) return;
    const interval = setInterval(() => void load(), pollMs);
    return () => clearInterval(interval);
  }, [load, pollMs, hasProcessing]);

  async function handleUpload(agentId: string, selected: File) {
    setFeedback(null);
    const extension = selected.name.slice(selected.name.lastIndexOf(".")).toLowerCase();
    if (![".pdf", ".docx", ".txt"].includes(extension)) {
      setFeedback("Formato não suportado — envie PDF, DOCX ou TXT.");
      return;
    }
    if (selected.size > MAX_FILE_BYTES) {
      setFeedback("Arquivo excede o limite de 20 MB.");
      return;
    }
    const form = new FormData();
    form.append("file", selected);
    form.append("agent_id", agentId);
    setUploading(true);
    try {
      const response = await backendFetch("knowledge-base/files", { method: "POST", body: form });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(body?.detail ?? "Falha no upload — tente novamente.");
        return;
      }
      await load();
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setUploading(false);
    }
  }

  async function handleAttach(fileId: string, agentId: string) {
    setFeedback(null);
    try {
      const response = await backendFetch(`agents/${agentId}/knowledge-base-files`, {
        method: "POST",
        body: JSON.stringify({ knowledge_base_file_id: fileId }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(body?.detail ?? "Falha ao anexar — tente novamente.");
        return;
      }
      await load();
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    }
  }

  async function handleDetach(agentId: string, fileId: string) {
    setFeedback(null);
    try {
      const response = await backendFetch(`agents/${agentId}/knowledge-base-files/${fileId}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(body?.detail ?? "Falha ao desanexar — tente novamente.");
        return;
      }
      await load();
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    }
  }

  async function handleDelete(file: KbFile) {
    if (!window.confirm(`Excluir "${file.filename}" da base de conhecimento?`)) return;
    try {
      const response = await backendFetch(`knowledge-base/files/${file.id}`, { method: "DELETE" });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(body?.detail ?? "Falha ao excluir — tente novamente.");
        return;
      }
      await load();
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    }
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      <header className="border-b border-line px-8 py-5">
        <h1 className="font-display text-xl font-semibold text-ink">Base de conhecimento</h1>
        <p className="text-sm text-muted">
          PDF, DOCX ou TXT, até 20 MB — organizada por agente. Um arquivo pode ser anexado a mais
          de um.
        </p>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-4">
        {agents.length === 0 && (
          <p className="py-10 text-center text-sm text-muted">Nenhum agente cadastrado ainda.</p>
        )}
        {agents.map((agent) => (
          <AgentFolder
            key={agent.id}
            agent={agent}
            files={files.filter((f) => f.agent_ids.includes(agent.id))}
            allAgents={agents}
            defaultExpanded={focusedAgentId ? agent.id === focusedAgentId : agent.is_entry_point}
            uploading={uploading}
            onUpload={handleUpload}
            onAttach={handleAttach}
            onDetach={handleDetach}
            onDelete={handleDelete}
          />
        ))}
      </div>
    </main>
  );
}
