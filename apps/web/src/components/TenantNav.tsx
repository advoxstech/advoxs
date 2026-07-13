"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { logout } from "@/app/conversas/actions";
import { backendFetch } from "@/lib/client-api";

type TenantNavItem = "inicio" | "conversas" | "base" | "config" | "cobranca" | "creditos" | "perfil";

const ITEMS: { key: TenantNavItem; href: string; label: string }[] = [
  { key: "inicio", href: "/inicio", label: "Início" },
  { key: "conversas", href: "/conversas", label: "Conversas" },
  { key: "base", href: "/base-de-conhecimento", label: "Base" },
  { key: "config", href: "/configuracoes/whatsapp", label: "Config" },
  { key: "cobranca", href: "/configuracoes/cobranca-clientes", label: "Cobrança" },
  { key: "creditos", href: "/creditos", label: "Créditos" },
  { key: "perfil", href: "/perfil", label: "Perfil" },
];

export function TenantNav({ active }: { active: TenantNavItem | null }) {
  const [hasLogo, setHasLogo] = useState(false);

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
    <nav className="flex w-14 shrink-0 flex-col items-center justify-between bg-ink py-5">
      <div className="flex flex-col items-center gap-6">
        {hasLogo ? (
          <img
            src="/api/backend/profile/logo"
            alt="Logo do escritório"
            className="h-8 w-8 rounded-sm object-cover"
          />
        ) : (
          <span className="font-display text-2xl font-semibold text-ground" aria-label="Advoxs">
            A.
          </span>
        )}
        {ITEMS.map((item) =>
          item.key === active ? (
            <span
              key={item.key}
              className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground [writing-mode:vertical-rl]"
            >
              {item.label}
            </span>
          ) : (
            <Link
              key={item.key}
              href={item.href}
              className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
            >
              {item.label}
            </Link>
          ),
        )}
      </div>
      <form action={logout}>
        <button
          type="submit"
          className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
        >
          Sair
        </button>
      </form>
    </nav>
  );
}
