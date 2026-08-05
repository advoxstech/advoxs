"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { backendFetch } from "@/lib/client-api";
import type { Agent } from "@/lib/types";

const EMPTY_FORM = { name: "", instructions: "" };

function extractErrorDetail(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export function AgentsPanel() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [creating, setCreating] = useState(false);

  async function load() {
    try {
      const response = await backendFetch("agents");
      if (response.ok) {
        setAgents(await response.json());
      }
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setCreating(true);
    try {
      const response = await backendFetch("agents", {
        method: "POST",
        body: JSON.stringify(form),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(body, "Falha ao criar agente — tente novamente."));
        return;
      }
      await load();
      setForm(EMPTY_FORM);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(agent: Agent) {
    if (!window.confirm(`Excluir o agente "${agent.name}"?`)) return;
    try {
      const response = await backendFetch(`agents/${agent.id}`, { method: "DELETE" });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(extractErrorDetail(body, "Falha ao excluir — tente novamente."));
        return;
      }
      setAgents(agents.filter((a) => a.id !== agent.id));
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

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      <header className="border-b border-line px-8 py-5">
        <h1 className="font-display text-xl font-semibold text-ink">Agentes</h1>
        <p className="text-sm text-muted">
          Um agente é uma &quot;persona&quot; de IA — tem um nome, um conjunto de instruções
          (o que ele sabe e como deve responder) e pode ter uma base de conhecimento própria. O
          agente marcado como ponto de entrada é quem recebe a primeira mensagem de qualquer
          contato novo pelo WhatsApp; a partir daí, ele decide se responde direto ou transfere
          pra outro agente do escritório.
        </p>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <ul className="max-w-md">
          {agents.length === 0 && (
            <li className="py-4 text-sm text-muted">Nenhum agente cadastrado ainda.</li>
          )}
          {agents.map((agent) => (
            <li
              key={agent.id}
              className="flex items-center justify-between border-b border-line py-3"
            >
              <div className="min-w-0 flex-1">
                <Link href={`/agentes/${agent.id}`} className="font-medium text-ink hover:underline">
                  {agent.name}
                </Link>
                {agent.is_entry_point && (
                  <span className="ml-2 rounded-full bg-accent-soft px-3 py-1 font-mono text-[10px] uppercase tracking-[0.15em] text-accent">
                    ponto de entrada
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={() => void handleDelete(agent)}
                className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
              >
                Excluir
              </button>
            </li>
          ))}
        </ul>

        <hr className="my-6 border-line" />

        <h2 className="font-display text-lg font-semibold text-ink">Criar agente</h2>
        <p className="mt-2 max-w-md text-sm text-muted">
          Não sabe por onde começar? Pense num atendente que você está treinando: dê um nome pra
          ele e escreva, em texto livre, quem ele é, o que ele deve saber e como deve se comportar
          nas conversas. Você pode editar as instruções quantas vezes quiser depois de criar.
        </p>
        <form onSubmit={handleCreate} className="mt-4 flex max-w-md flex-col gap-4">
          <label className="flex flex-col gap-1 text-sm text-ink">
            Nome
            <input
              required
              placeholder="Ex: Recepção, Direito de Família, Suporte Financeiro"
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-ink">
            Instruções
            <textarea
              required
              rows={6}
              placeholder={
                "Você é a recepcionista do escritório. Atenda com cordialidade, " +
                "faça a triagem inicial dos clientes e responda dúvidas simples sobre " +
                "horários e documentos necessários. Se a pergunta for sobre um caso " +
                "jurídico específico, avise que um advogado vai retornar em breve."
              }
              value={form.instructions}
              onChange={(event) => setForm({ ...form, instructions: event.target.value })}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <p className="-mt-2 text-xs text-muted">
            Escreva como se estivesse orientando alguém novo no escritório — quanto mais
            específico, melhor ele responde.
          </p>
          <button
            type="submit"
            disabled={creating}
            className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
          >
            {creating ? "Criando..." : "Criar agente"}
          </button>
        </form>
      </div>
    </main>
  );
}
