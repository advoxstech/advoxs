import Link from "next/link";

import { logout } from "@/app/conversas/actions";

type TenantNavItem = "inicio" | "conversas" | "base" | "config" | "creditos";

const ITEMS: { key: TenantNavItem; href: string; label: string }[] = [
  { key: "inicio", href: "/inicio", label: "Início" },
  { key: "conversas", href: "/conversas", label: "Conversas" },
  { key: "base", href: "/base-de-conhecimento", label: "Base" },
  { key: "config", href: "/configuracoes/whatsapp", label: "Config" },
  { key: "creditos", href: "/creditos", label: "Créditos" },
];

export function TenantNav({ active }: { active: TenantNavItem | null }) {
  return (
    <nav className="flex w-14 shrink-0 flex-col items-center justify-between bg-ink py-5">
      <div className="flex flex-col items-center gap-6">
        <span className="font-display text-2xl font-semibold text-ground" aria-label="Advoxs">
          A.
        </span>
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
