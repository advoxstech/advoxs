"use client";

import type { ReactNode } from "react";

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
  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden md:flex-row">
      <TenantNav active={active} urgentCount={urgentCount} />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">{children}</div>
      <MobileTabBar active={active} urgentCount={urgentCount} />
    </div>
  );
}
