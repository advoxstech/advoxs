"use client";

import { useEffect, useState } from "react";
import { TenantShell } from "@/components/TenantShell";
import { teamRequest } from "@/components/TeamPanel";

export default function Page() {
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    teamRequest("team/agents")
      .then(setAgents)
      .catch((e) => setError(e.message));
  }, []);
  return (
    <TenantShell active="meus-agentes">
      <main className="p-6 overflow-auto">
        <h1 className="font-display text-3xl mb-4">Meus agentes</h1>
        <p>
          Você recebe os alertas de urgência destes agentes. A IA continua
          atendendo até alguém assumir.
        </p>
        {error && <p role="alert">{error}</p>}
        <ul className="mt-5 space-y-3">
          {agents.map((a) => (
            <li key={a.id} className="border border-line p-4">
              {a.name}
            </li>
          ))}
        </ul>
        {!agents.length && !error && (
          <p className="mt-4">
            Nenhum agente vinculado. Peça ao administrador para configurar seus
            vínculos.
          </p>
        )}
      </main>
    </TenantShell>
  );
}
