"use client";

import { useEffect, useRef, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import { chooseDriveFiles, loadDrivePicker, type DriveConfig } from "@/lib/google-drive";
import { KB_CATEGORIES } from "@/lib/kb-categories";
import type { Agent } from "@/lib/types";

type Item = {
  file_id: string;
  filename: string;
  version?: string;
  action: "import" | "update" | "unchanged" | "pending" | "error";
  existing_file_id?: string;
  selected: boolean;
  message?: string;
  done?: boolean;
};

const ACTIONS = {
  import: "Novo documento",
  update: "Atualizar documento — mantém os agentes e a categoria atuais",
  unchanged: "Este arquivo já foi importado e não mudou",
  pending: "Já existe uma tentativa. Confira o processamento na lista",
  error: "Não foi possível importar",
};

async function request(path: string, data: object) {
  const response = await backendFetch(`knowledge-base/drive/${path}`, {
    method: "POST", body: JSON.stringify(data),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof body?.detail === "string"
    ? body.detail : "Falha de conexão. Tente novamente.");
  return body;
}

export function GoogleDriveImport({ agents, onImported }: {
  agents: Agent[];
  onImported: () => Promise<void>;
}) {
  const [config, setConfig] = useState<DriveConfig | null>(null);
  const [open, setOpen] = useState(false);
  const [sdkReady, setSdkReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [agentId, setAgentId] = useState("");
  const [category, setCategory] = useState("");
  const token = useRef<string | null>(null);
  const mounted = useRef(true);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    mounted.current = true;
    backendFetch("knowledge-base/drive/config").then(async (response) => {
      if (response.ok && mounted.current) setConfig(await response.json());
    }).catch(() => { /* Upload local continua disponível. */ });
    return () => { mounted.current = false; token.current = null; };
  }, []);

  useEffect(() => {
    if (!busy) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy]);

  async function prepare() {
    setError("");
    try { await loadDrivePicker(); if (mounted.current) setSdkReady(true); }
    catch (cause) { if (mounted.current) setError((cause as Error).message); }
  }

  function show() {
    setOpen(true);
    setAgentId(agents.find((agent) => agent.is_entry_point)?.id ?? agents[0]?.id ?? "");
    void prepare();
  }

  async function selectFiles() {
    if (!config || busy) return;
    setError(""); setBusy(true); setItems([]); token.current = null;
    try {
      const selection = await chooseDriveFiles(config);
      if (!selection || !mounted.current) return;
      token.current = selection.token;
      const rows: Item[] = [];
      for (const [index, file] of selection.files.entries()) {
        if (!mounted.current) break;
        setProgress(`Conferindo ${index + 1} de ${selection.files.length}…`);
        try {
          const preview = await request("preview", { access_token: selection.token, file_id: file.id });
          rows.push({ ...preview, selected: preview.action === "import" });
        } catch (cause) {
          rows.push({ file_id: file.id, filename: file.name, action: "error", selected: false,
            message: (cause as Error).message });
        }
      }
      if (mounted.current) setItems(rows);
    } catch (cause) {
      if (mounted.current) setError((cause as Error).message);
    } finally {
      if (mounted.current) { setBusy(false); setProgress(""); }
    }
  }

  async function importFiles() {
    if (!token.current || !agentId || busy) return;
    setBusy(true); setError("");
    const selected = items.filter((item) => item.selected && !item.done);
    try {
      for (const [index, item] of selected.entries()) {
        if (!mounted.current || !token.current) break;
        setProgress(`Importando ${index + 1} de ${selected.length}…`);
        let message: string;
        try {
          const result = await request("import", {
            access_token: token.current, file_id: item.file_id,
            expected_version: item.version, agent_id: agentId, category: category || null,
            replace_file_id: item.action === "update" ? item.existing_file_id : null,
          });
          message = result.result === "unchanged" ? "Este arquivo já foi importado"
            : result.result === "error" ? result.message
            : "Importado. Acompanhe o processamento na lista abaixo.";
        } catch (cause) { message = (cause as Error).message; }
        if (mounted.current) {
          setItems((current) => current.map((row) => row.file_id === item.file_id
            ? { ...row, done: true, selected: false, message } : row));
          await onImported();
        }
      }
    } finally {
      token.current = null;
      if (mounted.current) { setBusy(false); setProgress("Importação concluída. Confira os resultados."); }
    }
  }

  if (!config?.enabled) return null;
  return (
    <section aria-label="Importação do Google Drive" className="border-b border-line px-4 py-4 sm:px-8">
      {!open ? (
        <button ref={button} type="button" onClick={show} disabled={!agents.length}
          className="rounded border border-line bg-surface px-4 py-2 text-sm text-ink hover:border-accent disabled:opacity-50">
          Importar do Google Drive
        </button>
      ) : (
        <div className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <h2 className="font-display text-lg text-ink">Importar do Google Drive</h2>
            <button type="button" disabled={busy} onClick={() => {
              token.current = null; setOpen(false); setItems([]); setProgress("");
              window.setTimeout(() => button.current?.focus(), 0);
            }} className="px-3 py-2 text-sm text-muted disabled:opacity-50">Fechar</button>
          </div>
          <p className="text-sm text-muted">
            Uma cópia será adicionada à base. Alterações no Google Drive não serão atualizadas
            automaticamente. Aceita PDF, DOCX, TXT e Documentos Google, até 20 arquivos por vez.
          </p>
          <div className="flex flex-wrap gap-4">
            <label className="text-sm text-ink">Agente para novos documentos
              <select value={agentId} onChange={(event) => setAgentId(event.target.value)} disabled={busy}
                className="mt-1 block max-w-full rounded border border-line bg-surface p-2">
                {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
              </select>
            </label>
            <label className="text-sm text-ink">Categoria para novos documentos
              <select value={category} onChange={(event) => setCategory(event.target.value)} disabled={busy}
                className="mt-1 block max-w-full rounded border border-line bg-surface p-2">
                <option value="">Sem categoria</option>
                {KB_CATEGORIES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
          </div>
          {error && <p role="alert" className="text-sm text-danger">{error}</p>}
          {!sdkReady && error ? (
            <button type="button" onClick={() => void prepare()} className="text-sm text-accent">Tentar carregar novamente</button>
          ) : (
            <button type="button" onClick={() => void selectFiles()} disabled={busy || !sdkReady}
              className="rounded border border-line bg-surface px-4 py-2 text-sm text-ink disabled:opacity-50">
              {sdkReady ? "Selecionar arquivos no Google Drive" : "Carregando Google Drive…"}
            </button>
          )}
          {items.length > 0 && <ul className="divide-y divide-line">
            {items.map((item) => (
              <li key={item.file_id} className="py-3 text-sm">
                <label className="flex items-start gap-3 text-ink">
                  <input type="checkbox" checked={item.selected}
                    disabled={busy || item.done || !["import", "update"].includes(item.action)}
                    onChange={(event) => setItems((current) => current.map((row) => row.file_id === item.file_id
                      ? { ...row, selected: event.target.checked } : row))}
                    className="mt-1 accent-accent" />
                  <span className="min-w-0 break-words"><strong>{item.filename}</strong><br />
                    <span className="text-muted">{item.message ?? ACTIONS[item.action]}</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>}
          {items.some((item) => item.action === "update" && !item.done) && (
            <p className="text-sm text-muted">Marque as atualizações que deseja confirmar. A versão anterior
              continua disponível até o processamento terminar. O histórico ocupa armazenamento e
              mantém as fontes das respostas antigas.</p>
          )}
          {items.some((item) => !item.done && ["import", "update"].includes(item.action)) && (
            <button type="button" onClick={() => void importFiles()}
              disabled={busy || !agentId || !items.some((item) => item.selected) || !token.current}
              className="rounded bg-accent px-4 py-2 text-sm text-white disabled:opacity-50">
              Confirmar importação
            </button>
          )}
          <p role="status" aria-live="polite" className="text-sm text-muted">{progress}</p>
        </div>
      )}
    </section>
  );
}
