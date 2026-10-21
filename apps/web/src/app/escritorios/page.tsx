"use client";

import { useEffect, useState } from "react";
import { logout } from "@/app/conversas/actions";

type Office = { id: string; name: string; role: "admin" | "lawyer" };

export default function OfficesPage() {
  const [offices, setOffices] = useState<Office[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState<string | null>(null);
  const [conversation, setConversation] = useState<string | null>(null);
  const [page, setPage] = useState<string | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setTarget(params.get("escritorio"));
    setConversation(params.get("conversa"));
    setPage(params.get("destino"));
    let live = true;
    fetch("/api/escritorios", { cache: "no-store" }).then(async (r) => {
      if (r.status === 401) { window.location.assign("/login?next=" + encodeURIComponent(window.location.pathname + window.location.search)); return; }
      if (!r.ok) throw new Error("Não foi possível carregar seus escritórios.");
      const body = await r.json();
      if (live) setOffices(body.offices);
    }).catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, []);

  async function select(office: Office) {
    setBusy(true); setError("");
    try {
      const r = await fetch("/api/escritorios", { method: "POST",
        headers: { "content-type": "application/json" }, body: JSON.stringify({ tenant_id: office.id }) });
      if (!r.ok) throw new Error("Não foi possível abrir este escritório. Atualize a página e tente novamente.");
      try { localStorage.setItem("advoxs-office-change", `${office.id}:${Date.now()}`); }
      catch { /* Context headers still protect tabs when storage is unavailable. */ }
      const fromAlert = target === office.id;
      const destination = fromAlert && conversation
        ? `/conversas?conversa=${encodeURIComponent(conversation)}`
        : fromAlert && page === "creditos" && office.role === "admin"
          ? "/creditos"
          : office.role === "admin" ? "/inicio" : "/conversas";
      // Full navigation clears all tenant-bound state, caches and pending forms.
      window.location.assign(destination);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }
  return <main className="min-h-screen bg-paper p-6 flex justify-center items-center">
    <section className="w-full max-w-lg rounded border border-line bg-surface p-8 space-y-5">
      <h1 className="font-display text-3xl">Escolha o escritório</h1>
      <p className="text-muted">Cada escritório tem seus próprios agentes e atendimentos.</p>
      {error && <p role="alert" className="text-danger">{error}</p>}
      {!offices && !error && <p>Carregando…</p>}
      {offices?.length === 0 && <p>Você não possui acesso ativo a um escritório. Peça um convite ou a reativação ao administrador.</p>}
      {target && offices && !offices.some((o) => o.id === target) && <p role="alert">Você não tem acesso ao escritório deste link.</p>}
      {offices?.map((office) => <button key={office.id} disabled={busy} onClick={() => void select(office)}
        className="block w-full text-left p-4 rounded border border-line hover:bg-accent/10 disabled:opacity-50">
        <span className="block font-semibold">{office.name}</span>
        <span className="text-sm text-muted">{office.role === "admin" ? "Administrador" : "Advogado"}{target === office.id ? " · Escritório do alerta" : ""}</span>
      </button>)}
      <form action={logout}><button className="text-muted underline">Sair da conta</button></form>
    </section>
  </main>;
}
