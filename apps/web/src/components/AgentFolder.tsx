"use client";

import { useRef, useState } from "react";

import { formatPages } from "@/lib/format";
import {
  SPECIALTIES,
  UNCATEGORIZED_LABEL,
  kbCategoryOptions,
  kbTree,
  specialtyLabel,
  type KbFolder,
} from "@/lib/kb-categories";
import type { Agent } from "@/lib/types";

export type KbFile = {
  id: string;
  filename: string;
  size_bytes: number;
  mime_type: string;
  status: "processing" | "ready" | "error";
  error_message: string | null;
  category: string | null;
  uploaded_at: string;
  agent_ids: string[];
  drive_file_id?: string | null;
  imported_at?: string | null;
  replaces_file_id?: string | null;
  pages_total?: number | null;
  partial_pages?: number[];
  /** Cópia de um documento do pacote da especialidade (base Advoxs). */
  from_package?: boolean;
};

/** Aviso de leitura parcial (páginas sem texto ou com OCR ruim); null se completa. */
export function partialReadingText(file: KbFile): string | null {
  const pages = file.partial_pages ?? [];
  if (file.status !== "ready" || pages.length === 0) return null;
  const one = pages.length === 1;
  const total = file.pages_total ? ` de ${file.pages_total}` : "";
  return (
    `${one ? "1 página" : `${pages.length} páginas`}${total} ` +
    `${one ? "não foi lida ou ficou" : "não foram lidas ou ficaram"} com leitura ruim ` +
    `(p. ${formatPages(pages)}). Envie uma digitalização mais nítida dessas páginas ` +
    "para o agente consultá-las."
  );
}

const ACCEPTED = ".pdf,.docx,.txt";

const STATUS_LABEL: Record<KbFile["status"], string> = {
  processing: "processando",
  ready: "pronto",
  error: "erro",
};

const STATUS_CLASS: Record<KbFile["status"], string> = {
  processing: "bg-brass-soft text-brass-ink",
  ready: "bg-accent-soft text-accent",
  error: "bg-danger/10 text-danger",
};

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

function countLabel(count: number): string {
  if (count === 0) return "vazia";
  return `${count} documento${count === 1 ? "" : "s"}`;
}

type FileActions = {
  onAttach: (fileId: string, agentId: string) => void;
  onDetach: (agentId: string, fileId: string) => void;
  onDelete: (file: KbFile) => void;
  onReprocess: (file: KbFile) => void;
  onRecategorize: (fileId: string, category: string) => void;
};

export function AgentFolder({
  agent,
  files,
  allAgents,
  defaultExpanded,
  uploading,
  onUpload,
  onSpecialty,
  onRestorePackage,
  ...actions
}: {
  agent: Agent;
  files: KbFile[];
  allAgents: Agent[];
  defaultExpanded: boolean;
  uploading: boolean;
  onUpload: (agentId: string, files: File[], category: string) => void;
  onSpecialty: (agent: Agent, specialty: string) => void;
  onRestorePackage: (agent: Agent) => void;
} & FileActions) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [uploadCategory, setUploadCategory] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const tree = kbTree(agent.specialty);
  const options = kbCategoryOptions(agent.specialty);
  const known = new Set(options.map((option) => option.value));
  const byCategory = new Map<string, KbFile[]>();
  const uncategorized: KbFile[] = [];
  for (const file of files) {
    // Pasta que este agente não tem (ex.: pasta da área de outra especialidade).
    if (!file.category || !known.has(file.category)) {
      uncategorized.push(file);
      continue;
    }
    const bucket = byCategory.get(file.category);
    if (bucket) bucket.push(file);
    else byCategory.set(file.category, [file]);
  }

  return (
    <section className="border-b border-line py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="flex flex-1 items-center gap-2 text-left"
        >
          <span className="text-muted">{expanded ? "▾" : "▸"}</span>
          <span className="font-medium text-ink">{agent.name}</span>
          {agent.is_entry_point && (
            <span className="rounded-full bg-accent-soft px-2 py-0.5 font-mono text-micro uppercase tracking-[0.12em] text-accent">
              ponto de entrada
            </span>
          )}
          <span className="text-xs text-muted">
            [{files.length} arquivo{files.length === 1 ? "" : "s"}]
          </span>
        </button>
        <select
          value={uploadCategory}
          onChange={(event) => setUploadCategory(event.target.value)}
          aria-label={`Categoria do envio para ${agent.name}`}
          className="max-w-[16rem] rounded border border-line bg-surface px-2 py-1.5 text-xs text-ink"
        >
          <option value="">Sem categoria</option>
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <label
          className={`cursor-pointer whitespace-nowrap rounded border border-line bg-surface px-3 py-1.5 text-action text-ink transition-colors hover:border-accent ${uploading ? "pointer-events-none opacity-50" : ""}`}
        >
          + Enviar arquivos
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPTED}
            aria-label={`Enviar arquivo para ${agent.name}`}
            className="hidden"
            onChange={(event) => {
              const selected = event.target.files;
              if (selected && selected.length > 0) onUpload(agent.id, Array.from(selected), uploadCategory);
              if (inputRef.current) inputRef.current.value = "";
            }}
          />
        </label>
      </div>

      {expanded && (
        <div className="ml-6 mt-2">
          <div className="flex flex-wrap items-center gap-3 border-b border-line pb-3 text-sm">
            <label className="flex items-center gap-2 text-muted">
              Especialidade
              <select
                value={agent.specialty ?? ""}
                onChange={(event) => onSpecialty(agent, event.target.value)}
                aria-label={`Especialidade de ${agent.name}`}
                className="rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
              >
                <option value="">Sem especialidade</option>
                {SPECIALTIES.map((specialty) => (
                  <option key={specialty.value} value={specialty.value}>
                    {specialty.label}
                  </option>
                ))}
              </select>
            </label>
            {agent.specialty && (
              <button
                type="button"
                onClick={() => onRestorePackage(agent)}
                title="Traz de volta os documentos do pacote que foram excluídos e atualiza os que mudaram"
                className="text-action text-muted transition-colors hover:text-accent"
              >
                Restaurar pacote de {specialtyLabel(agent.specialty)}
              </button>
            )}
            <span className="text-xs text-muted">
              A especialidade traz a legislação e a jurisprudência da área (pastas Z.1 e Z.2), já
              prontas para o agente consultar.
            </span>
          </div>

          {tree.map((folder) => (
            <FolderNode
              key={folder.value}
              folder={folder}
              byCategory={byCategory}
              agent={agent}
              allAgents={allAgents}
              options={options}
              {...actions}
            />
          ))}
          {uncategorized.length > 0 && (
            <FolderGroup label={UNCATEGORIZED_LABEL} count={uncategorized.length} defaultExpanded>
              <FileList
                files={uncategorized}
                agent={agent}
                allAgents={allAgents}
                options={options}
                {...actions}
              />
            </FolderGroup>
          )}
        </div>
      )}
    </section>
  );
}

function totalIn(folder: KbFolder, byCategory: Map<string, KbFile[]>): number {
  return (
    (byCategory.get(folder.value)?.length ?? 0) +
    (folder.children ?? []).reduce((sum, child) => sum + totalIn(child, byCategory), 0)
  );
}

function FolderNode({
  folder,
  byCategory,
  agent,
  allAgents,
  options,
  ...actions
}: {
  folder: KbFolder;
  byCategory: Map<string, KbFile[]>;
  agent: Agent;
  allAgents: Agent[];
  options: { value: string; label: string }[];
} & FileActions) {
  const own = byCategory.get(folder.value) ?? [];
  const total = totalIn(folder, byCategory);
  return (
    // Pastas com documentos abrem sozinhas; as vazias ficam fechadas para a
    // árvore inteira caber na tela.
    <FolderGroup label={folder.label} count={total} note={folder.note} defaultExpanded={total > 0}>
      {(folder.children ?? []).map((child) => (
        <FolderNode
          key={child.value}
          folder={child}
          byCategory={byCategory}
          agent={agent}
          allAgents={allAgents}
          options={options}
          {...actions}
        />
      ))}
      {own.length > 0 && (
        <FileList files={own} agent={agent} allAgents={allAgents} options={options} {...actions} />
      )}
      {total === 0 && !folder.children && (
        <p className="ml-6 py-1 text-xs text-muted">Nenhum documento nesta pasta.</p>
      )}
    </FolderGroup>
  );
}

function FolderGroup({
  label,
  count,
  note,
  defaultExpanded,
  children,
}: {
  label: string;
  count: number;
  note?: string;
  defaultExpanded: boolean;
  children: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  return (
    <div className="border-b border-line py-2 last:border-b-0">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        className="flex w-full flex-wrap items-center gap-2 text-left"
      >
        <span className="text-muted">{expanded ? "▾" : "▸"}</span>
        <span className="text-sm font-medium text-ink">{label}</span>
        <span className="text-xs text-muted">({countLabel(count)})</span>
        {note && <span className="text-xs text-brass-ink">{note}</span>}
      </button>
      {expanded && <div className="ml-6 mt-1">{children}</div>}
    </div>
  );
}

function FileList({
  files,
  agent,
  allAgents,
  options,
  onAttach,
  onDetach,
  onDelete,
  onReprocess,
  onRecategorize,
}: {
  files: KbFile[];
  agent: Agent;
  allAgents: Agent[];
  options: { value: string; label: string }[];
} & FileActions) {
  return (
    <ul>
      {files.map((file) => {
        const availableToAttach = allAgents.filter(
          (a) => a.id !== agent.id && !file.agent_ids.includes(a.id),
        );
        const partial = partialReadingText(file);
        return (
          <li
            key={file.id}
            className="flex flex-wrap items-center gap-3 border-b border-line py-3 last:border-b-0"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium text-ink">{file.filename}</p>
              {file.drive_file_id && (
                <p className="text-xs text-muted">
                  Google Drive · {file.replaces_file_id ? "Atualização em preparação" : "Cópia importada"}
                  {file.imported_at && ` em ${new Date(file.imported_at).toLocaleDateString("pt-BR")}`}
                </p>
              )}
              <p className="text-xs text-muted">
                {formatSize(file.size_bytes)} · {new Date(file.uploaded_at).toLocaleDateString("pt-BR")}
              </p>
              {file.status === "error" && file.error_message && (
                <p className="mt-1 text-xs text-danger">{file.error_message}</p>
              )}
              {partial && <p className="mt-1 text-xs text-brass-ink">{partial}</p>}
            </div>
            {file.from_package && (
              <span
                title="Documento do pacote da especialidade. Não conta no limite do plano."
                className="rounded-full border border-line px-3 py-1 font-mono text-micro uppercase tracking-[0.12em] text-muted"
              >
                base Advoxs
              </span>
            )}
            <span
              className={`rounded-full px-3 py-1 font-mono text-micro uppercase tracking-[0.12em] ${STATUS_CLASS[file.status]}`}
            >
              {STATUS_LABEL[file.status]}
            </span>
            {partial && (
              <span className="rounded-full bg-brass-soft px-3 py-1 font-mono text-micro uppercase tracking-[0.12em] text-brass-ink">
                leitura parcial
              </span>
            )}
            <select
              value={file.category ?? ""}
              disabled={!!file.replaces_file_id}
              aria-label={`Categoria de ${file.filename}`}
              onChange={(event) => onRecategorize(file.id, event.target.value)}
              className="max-w-[14rem] rounded border border-line bg-surface px-2 py-1 text-xs text-ink"
            >
              <option value="">Sem categoria</option>
              {options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {availableToAttach.length > 0 && !file.replaces_file_id && (
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
                    {a.name}
                  </option>
                ))}
              </select>
            )}
            {file.status === "error" && (
              <button
                type="button"
                onClick={() => onReprocess(file)}
                aria-label={`Reprocessar ${file.filename}`}
                className="text-action text-muted transition-colors hover:text-accent"
              >
                Reprocessar
              </button>
            )}
            <button
              type="button"
              onClick={() => onDetach(agent.id, file.id)}
              disabled={file.agent_ids.length <= 1 || !!file.replaces_file_id}
              aria-label={`Desanexar ${file.filename} deste agente`}
              title={
                file.agent_ids.length <= 1
                  ? "Este é o único agente anexado — exclua o arquivo se não for mais usar"
                  : undefined
              }
              className="text-action text-muted transition-colors hover:text-danger disabled:opacity-40"
            >
              Desanexar
            </button>
            <button
              type="button"
              onClick={() => onDelete(file)}
              disabled={file.status === "processing"}
              aria-label={`Excluir ${file.filename}`}
              className="text-action text-muted transition-colors hover:text-danger disabled:opacity-40"
            >
              Excluir
            </button>
          </li>
        );
      })}
    </ul>
  );
}
