"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { logout } from "@/app/conversas/actions";

type Invitation = { office_name: string; email: string; role: string; existing: boolean };
export default function Page() {
  const [token, setToken] = useState("");
  const [invitation, setInvitation] = useState<Invitation | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const value = new URLSearchParams(window.location.hash.slice(1)).get("token")
      || sessionStorage.getItem("advoxs-invitation");
    if (value) {
      setToken(value);
      sessionStorage.setItem("advoxs-invitation", value);
    }
    window.history.replaceState(null, "", window.location.pathname);
    if (!value) { setError("Abra o link completo recebido por e-mail."); return; }
    let live = true;
    fetch("/api/convite", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: value, action: "inspect" }) }).then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Convite indisponível.");
        if (live) setInvitation(body);
      }).catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, []);
  return <main className="min-h-screen flex items-center justify-center p-6">
    <div className="max-w-md w-full bg-surface border border-line p-8 rounded space-y-5">
      <h1 className="font-display text-3xl">Convite para o escritório</h1>
      {done ? <>
        <p>Convite aceito. O acesso ao escritório foi adicionado à sua conta.</p>
        {invitation?.existing ? <Link href="/escritorios" className="text-accent underline">Acessar meus escritórios</Link>
          : <form action={logout}><input type="hidden" name="next" value="/escritorios" /><button className="text-accent underline">Entrar com minha nova conta</button></form>}
      </> : invitation ? <>
        <p><strong>{invitation.office_name}</strong> convidou {invitation.email} como {invitation.role === "admin" ? "administrador" : "advogado"}.</p>
        <form className="space-y-4" onSubmit={async (e) => {
          e.preventDefault(); setBusy(true); setError("");
          const data = new FormData(e.currentTarget);
          try {
            const r = await fetch("/api/convite", { method: "POST", headers: { "content-type": "application/json" },
              body: JSON.stringify({ token, ...(invitation.existing ? {} : { password: data.get("password") }) }) });
            const body = await r.json();
            if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Não foi possível aceitar o convite.");
            sessionStorage.removeItem("advoxs-invitation"); setDone(true);
          } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
        }}>
          {invitation.existing ? <p>Entre com sua conta atual e confirme abaixo. Sua senha e seus outros escritórios permanecem os mesmos.</p> :
            <label className="block">Nova senha<input name="password" type="password" autoComplete="new-password"
              minLength={8} maxLength={72} required className="block w-full border border-line rounded p-3 mt-2" /></label>}
          <button disabled={busy || !token} className="bg-accent text-white rounded px-4 py-3 disabled:opacity-50">
            {busy ? "Confirmando…" : "Aceitar convite"}
          </button>
        </form>
        {invitation.existing && <>
          <Link href="/login?next=/convite" className="block text-accent underline">Entrar com minha conta</Link>
          <form action={logout}><input type="hidden" name="next" value="/convite" /><button className="text-sm underline">Sair para usar outro e-mail</button></form>
        </>}
      </> : !error && <p>Carregando convite…</p>}
      {error && <p role="alert" className="text-danger">{error}</p>}
    </div>
  </main>;
}
