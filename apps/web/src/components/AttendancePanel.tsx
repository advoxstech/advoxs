"use client";

import { useEffect, useState } from "react";
import { teamRequest } from "./TeamPanel";
import { useTeamSession } from "./TeamSession";
import type { Conversation } from "@/lib/types";

export function AttendancePanel({
  conversation,
  onUpdate,
}: {
  conversation: Conversation;
  onUpdate: (c: Conversation) => void;
}) {
  const identity = useTeamSession();
  const [events, setEvents] = useState<
    {
      actor_name: string;
      action: string;
      target_name: string | null;
      created_at: string;
    }[]
  >([]);
  const [members, setMembers] = useState<
    { id: string; name: string; active: boolean }[]
  >([]);
  const [target, setTarget] = useState("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open) return;
    let live = true;
    teamRequest(`conversations/${conversation.id}/attendance`)
      .then((e) => {
        if (live) setEvents(e);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    if (identity?.role === "admin")
      teamRequest("team")
        .then((m) => {
          if (live) setMembers(m);
        })
        .catch((e) => {
          if (live) setError(e.message);
        });
    return () => {
      live = false;
    };
  }, [open, conversation.id, conversation.assigned_user_id, identity?.role]);
  const labels: Record<string, string> = {
    assumed: "assumiu o atendimento",
    returned_to_ai: "devolveu para IA",
    transferred: "transferiu para",
    urgency_marked: "Marcou como urgente",
    urgency_resolved: "Resolveu a urgência",
    member_deactivated: "desativou o acesso de",
  };
  return (
    <div className="border-b border-line px-4 py-2 text-sm">
      <p>
        {conversation.state === "human"
          ? conversation.assigned_user_name
            ? `Em atendimento por ${conversation.assigned_user_name}`
            : "Atendimento humano sem responsável — precisa ser assumido."
          : "IA disponível"}
      </p>
      <button
        className="underline text-accent"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "Fechar histórico" : "Histórico do atendimento"}
      </button>
      {open && (
        <div className="max-h-48 overflow-auto space-y-2 py-2">
          {events.map((e, i) => (
            <p key={i}>
              {e.actor_name} {labels[e.action] || e.action} {e.target_name} ·{" "}
              {new Date(e.created_at).toLocaleString("pt-BR")}
            </p>
          ))}
          {!events.length && <p>Nenhuma ação registrada.</p>}
          {identity?.role === "admin" && conversation.state === "human" && (
            <div className="flex gap-2">
              <select
                aria-label="Transferir atendimento para"
                value={target}
                onChange={(e) => setTarget(e.target.value)}
              >
                <option value="">Selecione o profissional</option>
                {members
                  .filter((m) => m.active)
                  .map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
              </select>
              <button
                disabled={!target || busy}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  try {
                    onUpdate(
                      await teamRequest(
                        `conversations/${conversation.id}/transfer`,
                        {
                          method: "POST",
                          body: JSON.stringify({ user_id: target }),
                        },
                      ),
                    );
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Transferir
              </button>
            </div>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
