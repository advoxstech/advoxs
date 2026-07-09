import Link from "next/link";

import { adminLogout } from "@/app/admin/actions";

type AdminNavItem = "dashboard" | "tenants" | "playground";

const ITEMS: { key: AdminNavItem; href: string; label: string }[] = [
  { key: "dashboard", href: "/admin", label: "Dashboard" },
  { key: "tenants", href: "/admin/tenants", label: "Tenants" },
  { key: "playground", href: "/admin/playground", label: "Playground" },
];

export function AdminNav({ active }: { active?: AdminNavItem | null }) {
  return (
    <nav className="flex w-14 shrink-0 flex-col items-center justify-between bg-ink py-5">
      <div className="flex flex-col items-center gap-6">
        <span className="font-display text-2xl font-semibold text-ground" aria-label="Admin">
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
      <form action={adminLogout}>
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
