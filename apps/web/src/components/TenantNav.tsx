"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";

import { logout } from "@/app/conversas/actions";
import { backendFetch } from "@/lib/client-api";

type TenantNavItem =
  | "inicio"
  | "conversas"
  | "base"
  | "agentes"
  | "config"
  | "cobranca"
  | "creditos"
  | "perfil";

const ITEMS: { key: TenantNavItem; href: string; label: string; icon: ReactNode }[] = [
  {
    key: "inicio",
    href: "/inicio",
    label: "Início",
    icon: (
      <path d="M3 9.5 12 3l9 6.5M5 9v11h14V9" />
    ),
  },
  {
    key: "conversas",
    href: "/conversas",
    label: "Conversas",
    icon: (
      <path d="M21 11.5a8.4 8.4 0 0 1-8.8 8.4 8.9 8.9 0 0 1-3.8-.8L3 20l1-5.3a8.3 8.3 0 0 1-1-4.1A8.4 8.4 0 0 1 11.8 3a8.3 8.3 0 0 1 8.5 8.2Z" />
    ),
  },
  {
    key: "base",
    href: "/base-de-conhecimento",
    label: "Base",
    icon: (
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z" />
    ),
  },
  {
    key: "agentes",
    href: "/agentes",
    label: "Agentes",
    icon: (
      <>
        <rect x="4" y="8" width="16" height="12" rx="2" />
        <path d="M12 8V4M9 13h.01M15 13h.01" />
      </>
    ),
  },
  {
    key: "config",
    href: "/configuracoes/whatsapp",
    label: "Config",
    icon: (
      <>
        <rect x="2" y="5" width="20" height="14" rx="2" />
        <path d="M2 10h20" />
      </>
    ),
  },
  {
    key: "cobranca",
    href: "/configuracoes/cobranca-clientes",
    label: "Cobrança",
    icon: (
      <>
        <circle cx="12" cy="8" r="4" />
        <path d="M4 21v-2a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v2" />
      </>
    ),
  },
  {
    key: "creditos",
    href: "/creditos",
    label: "Créditos",
    icon: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 3" />
      </>
    ),
  },
  {
    key: "perfil",
    href: "/perfil",
    label: "Perfil",
    icon: (
      <>
        <circle cx="12" cy="7" r="4" />
        <path d="M4 21c0-4.4 3.6-8 8-8s8 3.6 8 8" />
      </>
    ),
  },
];

function NavIcon({ children }: { children: ReactNode }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export function TenantNav({ active }: { active: TenantNavItem | null }) {
  const [hasLogo, setHasLogo] = useState(false);
  const [pinned, setPinned] = useState(false);

  useEffect(() => {
    async function loadProfile() {
      try {
        const response = await backendFetch("profile");
        if (response.ok) {
          const body = await response.json();
          setHasLogo(Boolean(body.has_logo));
        }
      } catch {
        // fail-safe silencioso — mantém o monograma
      }
    }
    void loadProfile();
  }, []);

  return (
    <nav
      className={`group flex shrink-0 flex-col overflow-hidden bg-nav-bg py-5 transition-[width] duration-150 ease-out ${
        pinned ? "w-[232px]" : "w-[72px] hover:w-[232px]"
      }`}
    >
      <div className="flex h-8 items-center px-[22px]">
        {hasLogo ? (
          <img
            src="/api/backend/profile/logo"
            alt="Logo do escritório"
            className="h-8 w-8 shrink-0 rounded-sm object-cover"
          />
        ) : (
          <span
            className="font-display text-2xl font-semibold text-nav-ink"
            aria-label="Advoxs"
          >
            A.
          </span>
        )}
      </div>

      <div className="mt-6 flex flex-1 flex-col gap-0.5 px-3">
        {ITEMS.map((item) =>
          item.key === active ? (
            <span
              key={item.key}
              className="flex h-10 items-center gap-3.5 rounded-md bg-nav-active px-[11px] text-sm font-semibold text-nav-ink"
            >
              <NavIcon>{item.icon}</NavIcon>
              <span
                className={`whitespace-nowrap opacity-0 transition-opacity duration-100 group-hover:opacity-100 ${pinned ? "opacity-100" : ""}`}
              >
                {item.label}
              </span>
            </span>
          ) : (
            <Link
              key={item.key}
              href={item.href}
              className="flex h-10 items-center gap-3.5 rounded-md px-[11px] text-sm font-medium text-nav-ink-muted transition-colors hover:bg-nav-bg-2 hover:text-nav-ink"
            >
              <NavIcon>{item.icon}</NavIcon>
              <span
                className={`whitespace-nowrap opacity-0 transition-opacity duration-100 group-hover:opacity-100 ${pinned ? "opacity-100" : ""}`}
              >
                {item.label}
              </span>
            </Link>
          ),
        )}
      </div>

      <div className="flex flex-col gap-0.5 border-t border-nav-bg-2 px-3 pt-3.5">
        <button
          type="button"
          onClick={() => setPinned((v) => !v)}
          className="flex h-9 items-center gap-3.5 rounded-md px-[11px] text-left text-[13px] font-medium text-nav-ink-muted transition-colors hover:bg-nav-bg-2 hover:text-nav-ink"
        >
          <NavIcon>
            <path d={pinned ? "m15 18-6-6 6-6" : "m9 18 6-6-6-6"} />
          </NavIcon>
          <span
            className={`whitespace-nowrap opacity-0 transition-opacity duration-100 group-hover:opacity-100 ${pinned ? "opacity-100" : ""}`}
          >
            {pinned ? "Recolher menu" : "Fixar menu aberto"}
          </span>
        </button>
        <form action={logout}>
          <button
            type="submit"
            className="flex h-9 w-full items-center gap-3.5 rounded-md px-[11px] text-left text-[13px] font-medium text-nav-ink-muted transition-colors hover:bg-nav-bg-2 hover:text-nav-ink"
          >
            <NavIcon>
              <>
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <path d="m16 17 5-5-5-5" />
                <path d="M21 12H9" />
              </>
            </NavIcon>
            <span
              className={`whitespace-nowrap opacity-0 transition-opacity duration-100 group-hover:opacity-100 ${pinned ? "opacity-100" : ""}`}
            >
              Sair
            </span>
          </button>
        </form>
      </div>
    </nav>
  );
}
