"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { backendFetch } from "@/lib/client-api";

type KbFile = {
  id: string;
  filename: string;
  size_bytes: number;
  mime_type: string;
  status: "processing" | "ready" | "error";
  error_message: string | null;
  uploaded_at: string;
};

const ACCEPTED = ".pdf,.docx,.txt";
const MAX_FILE_BYTES = 20 * 1024 * 1024;

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

export function KnowledgeBasePanel({ pollMs = 5000 }: { pollMs?: number }) {
  const [files, setFiles] = useState<KbFile[]>([]);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

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
    if (!pollMs) return;
    const interval = setInterval(() => void load(), pollMs);
    return () => clearInterval(interval);
  }, [load, pollMs]);

  async function handleUpload(selected: File) {
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
    setUploading(true);
    try {
      const response = await backendFetch("knowledge-base/files", {
        method: "POST",
        body: form,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(body?.detail ?? "Falha no upload — tente novamente.");
        return;
      }
      await load();
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function handleDelete(file: KbFile) {
    if (!window.confirm(`Excluir "${file.filename}" da base de conhecimento?`)) return;
    const response = await backendFetch(`knowledge-base/files/${file.id}`, { method: "DELETE" });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      setFeedback(body?.detail ?? "Falha ao excluir — tente novamente.");
      return;
    }
    await load();
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      <header className="flex items-center justify-between border-b border-line px-8 py-5">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">Base de conhecimento</h1>
          <p className="text-sm text-muted">
            PDF, DOCX ou TXT, até 20 MB — os agentes consultam esses documentos nas conversas.
          </p>
        </div>
        <label
          className={`cursor-pointer rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent ${uploading ? "pointer-events-none opacity-50" : ""}`}
        >
          {uploading ? "Enviando..." : "Enviar arquivo"}
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED}
            className="hidden"
            onChange={(event) => {
              const selected = event.target.files?.[0];
              if (selected) void handleUpload(selected);
            }}
          />
        </label>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <ul className="flex-1 overflow-y-auto px-8 py-4">
        {files.length === 0 && (
          <li className="py-10 text-center text-sm text-muted">
            Nenhum arquivo na base de conhecimento ainda.
          </li>
        )}
        {files.map((file) => (
          <li
            key={file.id}
            className="flex items-center gap-4 border-b border-line py-4 last:border-b-0"
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
            <button
              type="button"
              onClick={() => void handleDelete(file)}
              disabled={file.status === "processing"}
              className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger disabled:opacity-40"
            >
              Excluir
            </button>
          </li>
        ))}
      </ul>
    </main>
  );
}
