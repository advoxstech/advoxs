"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { backendFetch } from "@/lib/client-api";
import type { Agent, Conversation } from "@/lib/types";
import { AgentVersions } from "./AgentVersions";
import { TestConversationThread } from "./TestConversationThread";

type Workspace = {
  published: Agent;
  published_version: number;
  draft_revision: number;
  name: string;
  instructions: string;
  has_unpublished_changes: boolean;
};

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
  const [savedValues, setSavedValues] = useState({
    name: "",
    instructions: "",
  });
  const [saving, setSaving] = useState(false);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [tab, setTab] = useState("Configuração");
  const [description, setDescription] = useState("");
  const [confirmPublish, setConfirmPublish] = useState(false);
  const [success, setSuccess] = useState(false);
  const [test, setTest] = useState<Conversation | null>(null);
  const hasUnsavedChanges =
    name !== savedValues.name || instructions !== savedValues.instructions;

  const load = useCallback(async () => {
    try {
      const [agentsResponse, attachedResponse] = await Promise.all([
        backendFetch(`agents/${agentId}/workspace`),
        backendFetch(`agents/${agentId}/knowledge-base-files`),
      ]);
      if (agentsResponse.ok) {
        const found: Workspace = await agentsResponse.json();
        setWorkspace(found);
        setAgent(found.published);
        setName(found.name);
        setInstructions(found.instructions);
        setSavedValues({ name: found.name, instructions: found.instructions });
      }
      if (attachedResponse.ok) {
        setAttachedFiles(await attachedResponse.json());
      }
      if (
        (!agentsResponse.ok && agentsResponse.status !== 404) ||
        !attachedResponse.ok
      )
        setLoadError(true);
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
      if (
        !link ||
        link.target ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      ) {
        return;
      }
      const destination = new URL(link.href, window.location.href);
      if (
        destination.origin === window.location.origin &&
        destination.pathname !== window.location.pathname &&
        !window.confirm("Há alterações não salvas. Deseja sair sem salvar?")
      ) {
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
    await changeWorkspace(
      "draft",
      "PATCH",
      { name, instructions },
      "Rascunho salvo. O atendimento ainda usa a versão publicada.",
    );
  }

  async function changeWorkspace(
    path: string,
    method: string,
    values: object,
    message: string,
  ) {
    if (!workspace || saving) return;
    setFeedback(null);
    setSuccess(false);
    setSaving(true);
    try {
      const response = await backendFetch(`agents/${agentId}/${path}`, {
        method,
        body: JSON.stringify({
          ...values,
          expected_revision: workspace.draft_revision,
        }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(
          extractErrorDetail(body, "Falha ao salvar — tente novamente."),
        );
        return;
      }
      const updated = body as Workspace;
      setWorkspace(updated);
      setAgent(updated.published);
      setName(updated.name);
      setInstructions(updated.instructions);
      setSavedValues({
        name: updated.name,
        instructions: updated.instructions,
      });
      setTest(null);
      setConfirmPublish(false);
      setTab("Configuração");
      if (path === "publish") setDescription("");
      setSuccess(true);
      setFeedback(message);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setSaving(false);
    }
  }

  async function startTest() {
    if (!workspace || saving || hasUnsavedChanges) return;
    setSaving(true);
    setFeedback(null);
    setSuccess(false);
    try {
      const response = await backendFetch(`agents/${agentId}/tests`, {
        method: "POST",
        body: JSON.stringify({ expected_revision: workspace.draft_revision }),
      });
      const body = await response.json();
      if (!response.ok) {
        setFeedback(
          extractErrorDetail(body, "Não foi possível iniciar o teste."),
        );
        return;
      }
      setTest(body);
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
    <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      <header className="border-b border-line px-4 py-5 md:px-8">
        <Link href="/agentes" className="text-xs text-muted hover:text-ink">
          ← Agentes
        </Link>
        <h1 className="font-display text-xl font-semibold text-ink">
          {agent.name}
        </h1>
        <p className="mt-2 text-sm text-muted">
          Versão {workspace?.published_version} publicada ·{" "}
          {workspace?.has_unpublished_changes
            ? "Rascunho com alterações não publicadas"
            : "Rascunho igual à versão publicada"}
        </p>
        <nav
          aria-label="Seções do agente"
          className="mt-4 flex flex-wrap gap-4"
        >
          {["Configuração", "Testar", "Versões"].map((item) => (
            <button
              key={item}
              type="button"
              aria-pressed={tab === item}
              disabled={saving}
              onClick={() => setTab(item)}
              className={`rounded px-3 py-2 text-sm ${tab === item ? "bg-surface text-accent ring-1 ring-line" : "text-muted"}`}
            >
              {item}
            </button>
          ))}
        </nav>
      </header>

      {feedback && (
        <p
          role={success ? "status" : "alert"}
          aria-live={success ? "polite" : "assertive"}
          className={`border-b border-line px-8 py-3 text-sm ${
            success ? "text-accent" : "bg-danger/5 text-danger"
          }`}
        >
          {feedback}
        </p>
      )}

      {tab === "Configuração" && (
        <div className="flex-1 overflow-y-auto px-4 py-6 md:px-8">
          <p className="max-w-md text-sm text-muted">
            As instruções abaixo definem quem esse agente é e como ele responde
            nas conversas — escreva como se estivesse orientando alguém novo no
            escritório: quem ele é, o que deve saber, e quando deve avisar que
            vai transferir a conversa pra outro agente ou pra um humano.
          </p>
          {hasUnsavedChanges ? (
            <p role="status" className="mt-4 text-sm text-brass">
              Há alterações não salvas.
            </p>
          ) : null}
          <form
            onSubmit={handleSave}
            className="mt-4 flex max-w-md flex-col gap-4"
          >
            <label className="flex flex-col gap-1 text-sm text-ink">
              Nome
              <input
                required
                disabled={saving}
                maxLength={200}
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
                disabled={saving}
                rows={8}
                placeholder="Descreva o papel do agente, como deve responder e quando deve transferir o atendimento."
                value={instructions}
                onChange={(event) => setInstructions(event.target.value)}
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <button
              type="submit"
              disabled={
                saving ||
                !hasUnsavedChanges ||
                !name.trim() ||
                !instructions.trim()
              }
              className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
            >
              {saving ? "Salvando…" : "Salvar rascunho"}
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

          <section
            className="mt-6 max-w-md space-y-3 rounded border border-line p-4"
            aria-label="Publicação"
          >
            <p className="text-sm text-muted">
              Salvar o rascunho não altera o atendimento. Publicar aplica nome e
              instruções nas próximas execuções da IA; respostas em andamento
              mantêm a configuração anterior.
            </p>
            <label className="flex flex-col gap-1 text-sm">
              Descrição da mudança (opcional)
              <input
                maxLength={1000}
                value={description}
                disabled={saving}
                onChange={(event) => setDescription(event.target.value)}
                className="rounded border border-line bg-surface p-2"
              />
            </label>
            {hasUnsavedChanges && (
              <p className="text-xs text-muted">
                Salve o rascunho antes de testar ou publicar.
              </p>
            )}
            {!confirmPublish ? (
              <button
                type="button"
                disabled={
                  saving ||
                  hasUnsavedChanges ||
                  !workspace?.has_unpublished_changes
                }
                onClick={() => setConfirmPublish(true)}
                className="rounded border border-line px-4 py-2 text-sm text-accent disabled:opacity-50"
              >
                Publicar versão
              </button>
            ) : (
              <div className="space-y-2">
                <p className="text-sm">
                  Publicar a versão {(workspace?.published_version ?? 0) + 1}{" "}
                  para os clientes?
                </p>
                <div className="flex gap-4">
                  <button
                    type="button"
                    disabled={saving || hasUnsavedChanges}
                    onClick={() =>
                      void changeWorkspace(
                        "publish",
                        "POST",
                        { description },
                        "Versão publicada com sucesso.",
                      )
                    }
                    className="text-sm text-accent underline disabled:opacity-50"
                  >
                    Confirmar publicação
                  </button>
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => setConfirmPublish(false)}
                    className="text-sm underline"
                  >
                    Cancelar
                  </button>
                </div>
              </div>
            )}
          </section>

          <hr className="my-6 border-line" />

          <h2 className="font-display text-lg font-semibold text-ink">
            Base de conhecimento
          </h2>
          <p className="mt-4 max-w-md text-sm text-muted">
            {attachedFiles.length} arquivo
            {attachedFiles.length === 1 ? "" : "s"} anexado
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
      )}
      {tab === "Versões" && workspace && (
        <div className="flex-1 overflow-y-auto px-4 py-6 md:px-8">
          <AgentVersions
            agentId={agentId}
            published={agent}
            currentVersion={workspace.published_version}
            busy={saving}
            onRestore={(number) =>
              changeWorkspace(
                `versions/${number}/restore`,
                "POST",
                {},
                "Versão recuperada como rascunho. Revise, teste e publique quando estiver pronta.",
              )
            }
          />
        </div>
      )}
      {tab === "Testar" && (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="space-y-3 border-b border-line px-4 py-4 md:px-8">
            <p className="text-sm text-muted">
              Teste o rascunho salvo com a base de conhecimento atual. Nenhuma
              mensagem vai para o WhatsApp. Os testes consomem créditos do
              escritório.
            </p>
            {hasUnsavedChanges && (
              <p role="status" className="text-sm text-brass">
                Salve as alterações na aba Configuração antes de iniciar um
                teste.
              </p>
            )}
            <button
              type="button"
              disabled={saving || hasUnsavedChanges}
              onClick={() => void startTest()}
              className="rounded border border-line px-4 py-2 text-sm text-accent disabled:opacity-50"
            >
              {saving
                ? "Iniciando…"
                : test
                  ? "Iniciar novo teste"
                  : "Iniciar teste do rascunho"}
            </button>
          </div>
          {test && !hasUnsavedChanges && (
            <TestConversationThread
              key={test.id}
              conversation={test}
              onDeleted={() => setTest(null)}
            />
          )}
        </div>
      )}
    </main>
  );
}
