"use client";

import { useCallback, useEffect, useState } from "react";
import { backendFetch } from "@/lib/client-api";

type Member = {
  id: string;
  name: string;
  email: string;
  role: "admin" | "lawyer";
  active: boolean;
};
type Invitation = {
  id: string;
  name: string;
  email: string;
  role: "admin" | "lawyer";
  accepted_at: string | null;
  cancelled_at: string | null;
  expires_at: string;
};
type Notification = {
  id: string;
  kind: string;
  status: string;
  recipient_name: string | null;
  invitation_email: string | null;
  last_error: string | null;
};
const input = "rounded border border-line bg-surface p-2 text-sm";
const button =
  "rounded border border-line px-3 py-2 text-sm disabled:opacity-50";
const statusLabels: Record<string, string> = {
  pending: "Na fila",
  processing: "Enviando",
  sent: "Aceito pelo servidor de e-mail",
  failed: "Falha no envio",
  cancelled: "Cancelado",
};

export async function teamRequest(path: string, init?: RequestInit) {
  const response = await backendFetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : "Não foi possível concluir. Tente novamente.",
    );
  }
  return response.status === 204 ? null : response.json();
}

export function TeamPanel() {
  const [members, setMembers] = useState<Member[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    const [m, i, n] = await Promise.all([
      teamRequest("team"),
      teamRequest("team/invitations"),
      teamRequest("team/notifications"),
    ]);
    setMembers(m);
    setInvitations(i);
    setNotifications(n);
  }, []);
  useEffect(() => {
    void load().catch((e) => setError(e.message));
  }, [load]);
  async function act(work: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
      await load();
      setNotice(message);
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="flex-1 overflow-auto p-6 space-y-8">
      <h1 className="font-display text-3xl">Equipe do escritório</h1>
      <p>
        Convide profissionais e gerencie o acesso. Os advogados vinculados a um
        agente recebem seus alertas de urgência.
      </p>
      {error && (
        <p role="alert" className="text-danger">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="text-accent">
          {notice}
        </p>
      )}
      <form
        className="flex flex-wrap gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          const form = e.currentTarget;
          const data = new FormData(form);
          void act(
            () =>
              teamRequest("team/invitations", {
                method: "POST",
                body: JSON.stringify(Object.fromEntries(data)),
              }),
            "Convite colocado na fila de envio.",
          ).then((success) => {
            if (success) form.reset();
          });
        }}
      >
        <input
          className={input}
          name="name"
          aria-label="Nome"
          placeholder="Nome"
          required
          maxLength={160}
        />
        <input
          className={input}
          name="email"
          aria-label="E-mail"
          placeholder="E-mail"
          type="email"
          required
        />
        <select
          className={input}
          name="role"
          aria-label="Perfil"
          defaultValue="lawyer"
        >
          <option value="lawyer">Advogado</option>
          <option value="admin">Administrador</option>
        </select>
        <button className={button} disabled={busy}>
          Enviar convite
        </button>
      </form>
      <section>
        <h2 className="font-display text-xl mb-3">Membros</h2>
        <div className="space-y-3">
          {members.map((m) => (
            <article
              key={m.id}
              className="rounded border border-line bg-surface p-4 flex flex-wrap items-center gap-3"
            >
              <div className="flex-1">
                <strong>{m.name}</strong>
                <p className="text-sm text-muted">
                  {m.email} · {m.active ? "Ativo" : "Desativado"}
                </p>
              </div>
              <select
                className={input}
                aria-label={`Perfil de ${m.name}`}
                value={m.role}
                disabled={busy}
                onChange={(e) => {
                  const role = e.target.value;
                  void act(
                    () =>
                      teamRequest(`team/members/${m.id}`, {
                        method: "PATCH",
                        body: JSON.stringify({ role, active: m.active }),
                      }),
                    "Perfil atualizado.",
                  );
                }}
              >
                <option value="lawyer">Advogado</option>
                <option value="admin">Administrador</option>
              </select>
              <button
                className={button}
                disabled={busy}
                onClick={() => {
                  if (
                    window.confirm(
                      `${m.active ? "Desativar" : "Reativar"} acesso de ${m.name}?`,
                    )
                  )
                    void act(
                      () =>
                        teamRequest(`team/members/${m.id}`, {
                          method: "PATCH",
                          body: JSON.stringify({
                            role: m.role,
                            active: !m.active,
                          }),
                        }),
                      "Acesso atualizado.",
                    );
                }}
              >
                {m.active ? "Desativar" : "Reativar"}
              </button>
            </article>
          ))}
        </div>
      </section>
      <section>
        <h2 className="font-display text-xl mb-3">Convites</h2>
        {invitations.map((i) => (
          <div
            key={i.id}
            className="border-b border-line py-3 flex flex-wrap gap-3 items-center"
          >
            <span className="flex-1">
              {i.name} · {i.email} ·{" "}
              {i.accepted_at
                ? "Aceito"
                : i.cancelled_at
                  ? "Cancelado"
                  : new Date(i.expires_at) < new Date()
                    ? "Expirado"
                    : "Pendente"}
            </span>
            {!i.accepted_at && !i.cancelled_at && (
              <>
                <button
                  className={button}
                  disabled={busy}
                  onClick={() =>
                    void act(
                      () =>
                        teamRequest("team/invitations", {
                          method: "POST",
                          body: JSON.stringify({
                            name: i.name,
                            email: i.email,
                            role: i.role,
                          }),
                        }),
                      "Novo convite solicitado; o link anterior foi invalidado.",
                    )
                  }
                >
                  Reenviar
                </button>
                <button
                  className={button}
                  disabled={busy}
                  onClick={() =>
                    void act(
                      () =>
                        teamRequest(`team/invitations/${i.id}`, {
                          method: "DELETE",
                        }),
                      "Convite cancelado.",
                    )
                  }
                >
                  Cancelar
                </button>
              </>
            )}
          </div>
        ))}
      </section>
      <section>
        <div className="flex gap-4 items-center">
          <h2 className="font-display text-xl">Envios recentes</h2>
          <button
            className={button}
            disabled={busy}
            onClick={() => void act(load, "Lista atualizada.")}
          >
            Atualizar
          </button>
        </div>
        {notifications.map((n) => (
          <div key={n.id} className="border-b border-line py-3 flex gap-3">
            <span className="flex-1">
              {n.kind === "urgency" ? "Urgência" : "Convite"} ·{" "}
              {n.recipient_name || n.invitation_email} ·{" "}
              {statusLabels[n.status] || n.status}
            </span>
            {n.status === "failed" && (
              <button
                className={button}
                disabled={busy}
                onClick={() =>
                  void act(
                    () =>
                      teamRequest(`team/notifications/${n.id}/retry`, {
                        method: "POST",
                      }),
                    "Nova tentativa solicitada.",
                  )
                }
              >
                Tentar novamente
              </button>
            )}
          </div>
        ))}
      </section>
    </main>
  );
}

export function AgentLawyers({ agentId }: { agentId: string }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(true);
  useEffect(() => {
    let live = true;
    Promise.all([
      teamRequest("team"),
      teamRequest(`team/agents/${agentId}/lawyers`),
    ])
      .then(([m, s]) => {
        if (!Array.isArray(m) || !Array.isArray(s))
          throw new Error("Não foi possível carregar os responsáveis.");
        if (live) {
          setMembers(m);
          setSelected(s);
          setBusy(false);
        }
      })
      .catch((e) => {
        if (live) setMessage(e.message);
      });
    return () => {
      live = false;
    };
  }, [agentId]);
  return (
    <section className="rounded border border-line bg-surface p-4 space-y-3">
      <h2 className="font-display text-xl">Advogados responsáveis</h2>
      <p className="text-sm text-muted">
        Todos os selecionados recebem os alertas, com a mesma responsabilidade.
        Sem advogado ativo, os administradores são avisados.
      </p>
      {members
        .filter((m) => m.role === "lawyer" && m.active)
        .map((m) => (
          <label key={m.id} className="flex gap-2">
            <input
              type="checkbox"
              disabled={busy}
              checked={selected.includes(m.id)}
              onChange={(e) =>
                setSelected((s) =>
                  e.target.checked
                    ? [...s, m.id]
                    : s.filter((id) => id !== m.id),
                )
              }
            />
            {m.name}
          </label>
        ))}
      <button
        className={button}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await teamRequest(`team/agents/${agentId}/lawyers`, {
              method: "PUT",
              body: JSON.stringify({
                user_ids: selected.filter((id) =>
                  members.some(
                    (m) => m.id === id && m.active && m.role === "lawyer",
                  ),
                ),
              }),
            });
            setMessage("Responsáveis atualizados.");
          } catch (e) {
            setMessage((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        Salvar responsáveis
      </button>
      {message && <p role="status">{message}</p>}
    </section>
  );
}
