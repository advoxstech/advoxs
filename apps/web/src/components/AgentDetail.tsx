"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
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
  const [loadError, setLoadError] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [savedValues, setSavedValues] = useState({ name: "", instructions: "" });
  const [saving, setSaving] = useState(false);
  const hasUnsavedChanges = name !== savedValues.name || instructions !== savedValues.instructions;

  const load = useCallback(async () => {
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
          setSavedValues({ name: found.name, instructions: found.instructions });
        }
      }
      if (attachedResponse.ok) {
        setAttachedFiles(await attachedResponse.json());
      }
      if (!agentsResponse.ok || !attachedResponse.ok) setLoadError(true);
    } catch {
      setLoadError(true);
    } finally {
      setLoaded(true);
    }
  }, [agentId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!hasUnsavedChanges) return;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const confirmInternalNavigation = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const link = target.closest<HTMLAnchorElement>("a[href]");
      if (!link || link.target || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      const destination = new URL(link.href, window.location.href);
      if (destination.origin === window.location.origin &&
          destination.pathname !== window.location.pathname &&
          !window.confirm("Há alterações não salvas. Deseja sair sem salvar?")) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    document.addEventListener("click", confirmInternalNavigation, true);
    return () => {
      window.removeEventListener("beforeunload", warnBeforeUnload);
      document.removeEventListener("click", confirmInternalNavigation, true);
    };
  }, [hasUnsavedChanges]);

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
      const savedName = typeof body?.name === "string" ? body.name : name;
      const savedInstructions =
        typeof body?.instructions === "string" ? body.instructions : instructions;
      setName(savedName);
      setInstructions(savedInstructions);
      setSavedValues({ name: savedName, instructions: savedInstructions });
      setFeedback("Alterações salvas com sucesso.");
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
        {loadError
          ? "Não foi possível carregar o agente. Atualize a página e tente novamente."
          : "Agente não encontrado."}{" "}
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
        <p
          role={feedback === "Alterações salvas com sucesso." ? "status" : "alert"}
          aria-live={feedback === "Alterações salvas com sucesso." ? "polite" : "assertive"}
          className={`border-b border-line px-8 py-3 text-sm ${
            feedback === "Alterações salvas com sucesso."
              ? "text-accent"
              : "bg-danger/5 text-danger"
          }`}
        >
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <p className="max-w-md text-sm text-muted">
          As instruções abaixo definem quem esse agente é e como ele responde nas conversas —
          escreva como se estivesse orientando alguém novo no escritório: quem ele é, o que deve
          saber, e quando deve avisar que vai transferir a conversa pra outro agente ou pra um
          humano.
        </p>
        {hasUnsavedChanges ? (
          <p role="status" className="mt-4 text-sm text-brass">
            Há alterações não salvas.
          </p>
        ) : null}
        <form onSubmit={handleSave} className="mt-4 flex max-w-md flex-col gap-4">
          <label className="flex flex-col gap-1 text-sm text-ink">
            Nome
            <input
              required
              placeholder="Ex.: Recepção"
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
              placeholder="Descreva o papel do agente, como deve responder e quando deve transferir o atendimento."
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <button
            type="submit"
            disabled={saving || !hasUnsavedChanges || !name.trim() || !instructions.trim()}
            className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
          >
            {saving ? "Salvando…" : "Salvar alterações"}
          </button>
          <button
            type="button"
            disabled={saving || !hasUnsavedChanges}
            onClick={() => {
              setName(savedValues.name);
              setInstructions(savedValues.instructions);
              setFeedback(null);
            }}
            className="self-start text-xs text-muted underline disabled:opacity-50"
          >
            Descartar alterações
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
