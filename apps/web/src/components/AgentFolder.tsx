"use client";

import { useRef, useState } from "react";

import type { Agent } from "@/lib/types";

export type KbFile = {
  id: string;
  filename: string;
  size_bytes: number;
  mime_type: string;
  status: "processing" | "ready" | "error";
  error_message: string | null;
  uploaded_at: string;
  agent_ids: string[];
};

const ACCEPTED = ".pdf,.docx,.txt";

const STATUS_LABEL: Record<KbFile["status"], string> = {
  processing: "processando",
  ready: "pronto",
  error: "erro",
};

const STATUS_CLASS: Record<KbFile["status"], string> = {
  processing: "bg-brass-soft text-brass",
  ready: "bg-accent-soft text-accent",
  error: "bg-danger/10 text-danger",
};

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

export function AgentFolder({
  agent,
  files,
  allAgents,
  defaultExpanded,
  uploading,
  onUpload,
  onAttach,
  onDetach,
  onDelete,
}: {
  agent: Agent;
  files: KbFile[];
  allAgents: Agent[];
  defaultExpanded: boolean;
  uploading: boolean;
  onUpload: (agentId: string, file: File) => void;
  onAttach: (fileId: string, agentId: string) => void;
  onDetach: (agentId: string, fileId: string) => void;
  onDelete: (file: KbFile) => void;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <section className="border-b border-line py-3">
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="flex flex-1 items-center gap-2 text-left"
        >
          <span className="text-muted">{expanded ? "▾" : "▸"}</span>
          <span className="font-medium text-ink">{agent.name}</span>
          {agent.is_entry_point && (
            <span className="rounded-full bg-accent-soft px-2 py-0.5 font-mono text-[9px] uppercase tracking-[0.15em] text-accent">
              ponto de entrada
            </span>
          )}
          <span className="text-xs text-muted">
            [{files.length} arquivo{files.length === 1 ? "" : "s"}]
          </span>
        </button>
        <label
          className={`cursor-pointer whitespace-nowrap rounded border border-line bg-surface px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent ${uploading ? "pointer-events-none opacity-50" : ""}`}
        >
          + Enviar arquivo
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED}
            aria-label={`Enviar arquivo para ${agent.name}`}
            className="hidden"
            onChange={(event) => {
              const selected = event.target.files?.[0];
              if (selected) onUpload(agent.id, selected);
              if (inputRef.current) inputRef.current.value = "";
            }}
          />
        </label>
      </div>

      {expanded && (
        <ul className="ml-6 mt-2">
          {files.length === 0 && (
            <li className="py-2 text-sm text-muted">Nenhum arquivo anexado ainda.</li>
          )}
          {files.map((file) => {
            const availableToAttach = allAgents.filter(
              (a) => a.id !== agent.id && !file.agent_ids.includes(a.id),
            );
            return (
              <li
                key={file.id}
                className="flex items-center gap-3 border-b border-line py-3 last:border-b-0"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-ink">{file.filename}</p>
                  <p className="text-xs text-muted">
                    {formatSize(file.size_bytes)} ·{" "}
                    {new Date(file.uploaded_at).toLocaleDateString("pt-BR")}
                  </p>
                  {file.status === "error" && file.error_message && (
                    <p className="mt-1 text-xs text-danger">{file.error_message}</p>
                  )}
                </div>
                <span
                  className={`rounded-full px-3 py-1 font-mono text-[10px] uppercase tracking-[0.15em] ${STATUS_CLASS[file.status]}`}
                >
                  {STATUS_LABEL[file.status]}
                </span>
                {availableToAttach.length > 0 && (
                  <select
                    value=""
                    aria-label={`Anexar ${file.filename} a outro agente`}
                    onChange={(event) => {
                      if (event.target.value) onAttach(file.id, event.target.value);
                    }}
                    className="rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
                  >
                    <option value="">+ anexar a outro agente</option>
                    {availableToAttach.map((a) => (
                      <option key={a.id} value={a.id}>
                        + {a.name}
                      </option>
                    ))}
                  </select>
                )}
                <button
                  type="button"
                  onClick={() => onDetach(agent.id, file.id)}
                  disabled={file.agent_ids.length <= 1}
                  aria-label={`Desanexar ${file.filename} deste agente`}
                  title={
                    file.agent_ids.length <= 1
                      ? "Este é o único agente anexado — exclua o arquivo se não for mais usar"
                      : undefined
                  }
                  className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger disabled:opacity-40"
                >
                  Desanexar
                </button>
                <button
                  type="button"
                  onClick={() => onDelete(file)}
                  disabled={file.status === "processing"}
                  aria-label={`Excluir ${file.filename}`}
                  className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger disabled:opacity-40"
                >
                  Excluir
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
