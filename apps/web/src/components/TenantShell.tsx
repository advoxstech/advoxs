"use client";

import { useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { TeamSession, type TeamIdentity } from "./TeamSession";
import { backendFetch } from "@/lib/client-api";

import { useUrgentCount } from "@/hooks/useUrgentCount";

import { MobileTabBar } from "./MobileTabBar";
import { TenantNav, type TenantNavItem } from "./TenantNav";

/** Layout do painel: menu lateral no desktop, barra inferior no celular. */
export function TenantShell({
  active,
  children,
}: {
  active: TenantNavItem | null;
  children: ReactNode;
}) {
  const urgentCount = useUrgentCount();
  const [identity, setIdentity] = useState<TeamIdentity | null>(null);
  const [error, setError] = useState(false);
  const pathname = usePathname();
  const router = useRouter();
  const lawyerAllowed = [
    "/conversas",
    "/meus-agentes",
    "/minha-conta",
  ].includes(pathname);
  useEffect(() => {
    let live = true;
    backendFetch("team/me")
      .then(async (r) => {
        if (!r.ok) throw new Error();
        const value = await r.json();
        if (live) setIdentity(value);
      })
      .catch(() => {
        if (live) setError(true);
      });
    return () => {
      live = false;
    };
  }, [pathname]);
  useEffect(() => {
    if (identity?.role === "lawyer" && !lawyerAllowed)
      router.replace("/conversas");
  }, [identity, lawyerAllowed, router]);
  if (error)
    return (
      <p role="alert" className="p-6">
        Não foi possível verificar seu acesso.{" "}
        <button onClick={() => window.location.reload()}>
          Tentar novamente
        </button>
      </p>
    );
  if (!identity || (identity.role === "lawyer" && !lawyerAllowed))
    return <p className="p-6">Carregando seu acesso…</p>;
  return (
    <TeamSession.Provider value={identity}>
      <div className="flex h-[100dvh] flex-col overflow-hidden md:flex-row">
        <TenantNav active={active} urgentCount={urgentCount} />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          {children}
        </div>
        <MobileTabBar active={active} urgentCount={urgentCount} />
      </div>
    </TeamSession.Provider>
  );
}
