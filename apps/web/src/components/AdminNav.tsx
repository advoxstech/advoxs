"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useState } from "react";

import { adminLogout } from "@/app/admin/actions";

type AdminNavItem = "dashboard" | "tenants" | "playground";

const ITEMS: { key: AdminNavItem; href: string; label: string; icon: ReactNode }[] = [
  {
    key: "dashboard",
    href: "/admin",
    label: "Dashboard",
    icon: (
      <>
        <rect x="3" y="3" width="7" height="9" rx="1" />
        <rect x="14" y="3" width="7" height="5" rx="1" />
        <rect x="14" y="12" width="7" height="9" rx="1" />
        <rect x="3" y="16" width="7" height="5" rx="1" />
      </>
    ),
  },
  {
    key: "tenants",
    href: "/admin/tenants",
    label: "Tenants",
    icon: (
      <>
        <path d="M3 21V7l7-4 7 4v14" />
        <path d="M13 21V11h4v10" />
        <path d="M7 9h.01M7 13h.01M7 17h.01" />
      </>
    ),
  },
  {
    key: "playground",
    href: "/admin/playground",
    label: "Playground",
    icon: (
      <path d="M21 11.5a8.4 8.4 0 0 1-8.8 8.4 8.9 8.9 0 0 1-3.8-.8L3 20l1-5.3a8.3 8.3 0 0 1-1-4.1A8.4 8.4 0 0 1 11.8 3a8.3 8.3 0 0 1 8.5 8.2Z" />
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

export function AdminNav({ active }: { active?: AdminNavItem | null }) {
  const [pinned, setPinned] = useState(false);

  return (
    <nav
      className={`group flex shrink-0 flex-col overflow-hidden bg-nav-bg py-5 transition-[width] duration-150 ease-out ${
        pinned ? "w-[232px]" : "w-[72px] hover:w-[232px]"
      }`}
    >
      <div className="flex h-8 items-center px-[22px]">
        <span className="font-display text-2xl font-semibold text-nav-ink" aria-label="Admin">
          A.
        </span>
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
        <form action={adminLogout}>
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
