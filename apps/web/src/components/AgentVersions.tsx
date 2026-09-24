"use client";

import { useEffect, useState } from "react";
import { backendFetch } from "@/lib/client-api";
import type { Agent } from "@/lib/types";

type Version = {
  number: number;
  name: string;
  instructions: string;
  author_name: string | null;
  description: string;
  restored_from_version: number | null;
  created_at: string;
};

export function AgentVersions({
  agentId,
  published,
  currentVersion,
  busy,
  onRestore,
}: {
  agentId: string;
  published: Agent;
  currentVersion: number;
  busy: boolean;
  onRestore: (number: number) => Promise<void>;
}) {
  const [versions, setVersions] = useState<Version[]>([]);
  const [selected, setSelected] = useState<Version | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [hasMore, setHasMore] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    backendFetch(`agents/${agentId}/versions`)
      .then(async (response) => {
        if (!response.ok) throw new Error();
        const data: Version[] = await response.json();
        if (!cancelled) {
          setVersions(data);
          setHasMore(data.length === 20);
          setError(false);
        }
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId, currentVersion]);

  async function loadMore() {
    setLoading(true);
    setError(false);
    try {
      const last = versions.at(-1);
      const response = await backendFetch(
        `agents/${agentId}/versions${last ? `?before=${last.number}` : ""}`,
      );
      if (!response.ok) throw new Error();
      const data: Version[] = await response.json();
      setVersions((previous) => [...previous, ...data]);
      setHasMore(data.length === 20);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="space-y-4" aria-label="Histórico de versões">
      <p className="text-sm text-muted">
        O histórico guarda nome e instruções. Documentos, vínculos da base e
        modelos de IA não são restaurados; as respostas podem variar com essas
        mudanças.
      </p>
      <ul className="space-y-2">
        {versions.map((version) => (
          <li key={version.number} className="rounded border border-line p-3">
            <button
              type="button"
              className="text-left text-sm text-accent underline"
              onClick={() => {
                setSelected(version);
                setConfirm(false);
              }}
            >
              Versão {version.number}
              {version.number === currentVersion ? " — publicada" : ""}
            </button>
            <p className="text-xs text-muted">
              {new Date(version.created_at).toLocaleString("pt-BR")} ·{" "}
              {version.author_name ?? "Sistema / autor indisponível"}
            </p>
            {version.description && (
              <p className="whitespace-pre-wrap break-words text-sm">
                {version.description}
              </p>
            )}
            {version.restored_from_version && (
              <p className="text-xs text-muted">
                Recuperada da versão {version.restored_from_version}
              </p>
            )}
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="text-sm text-danger">
          Não foi possível carregar o histórico.
        </p>
      )}
      {(hasMore || error) && (
        <button
          type="button"
          disabled={loading}
          onClick={() => void loadMore()}
          className="text-sm text-accent underline disabled:opacity-50"
        >
          {loading
            ? "Carregando…"
            : error
              ? "Tentar novamente"
              : "Carregar mais versões"}
        </button>
      )}
      {selected && (
        <div className="space-y-3 rounded border border-line p-4">
          <h2 className="font-semibold">Comparação com a versão publicada</h2>
          <div className="grid min-w-0 gap-4 md:grid-cols-2">
            {[
              { label: `Versão ${selected.number}`, ...selected },
              { label: `Publicada — versão ${currentVersion}`, ...published },
            ].map((item) => (
              <div key={item.label} className="min-w-0">
                <h3 className="text-sm text-accent">{item.label}</h3>
                <p className="break-words font-semibold">{item.name}</p>
                <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-words font-sans text-sm">
                  {item.instructions}
                </pre>
              </div>
            ))}
          </div>
          {!confirm ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirm(true)}
              className="text-sm text-accent underline"
            >
              Restaurar como rascunho
            </button>
          ) : (
            <div className="space-y-2 rounded border border-brass p-3">
              <p role="alert" className="text-sm">
                Isso substituirá o rascunho atual e as alterações não salvas. O
                atendimento só mudará depois de publicar.
              </p>
              <div className="flex gap-4">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void onRestore(selected.number)}
                  className="text-sm text-accent underline"
                >
                  Confirmar restauração
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setConfirm(false)}
                  className="text-sm underline"
                >
                  Cancelar
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
