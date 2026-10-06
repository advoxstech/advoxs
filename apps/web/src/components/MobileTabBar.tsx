"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { logout } from "@/app/conversas/actions";

import { ITEMS, NavIcon, UrgentBadge, type TenantNavItem } from "./TenantNav";

const PRIMARY: TenantNavItem[] = ["inicio", "conversas", "base", "agentes"];

/** Navegação do celular: 4 destinos principais + "Mais" com o restante. */
export function MobileTabBar({
  active,
  urgentCount = 0,
}: {
  active: TenantNavItem | null;
  urgentCount?: number;
}) {
  const [moreOpen, setMoreOpen] = useState(false);
  const moreButtonRef = useRef<HTMLButtonElement>(null);
  const primary = ITEMS.filter((item) => PRIMARY.includes(item.key));
  const secondary = ITEMS.filter((item) => !PRIMARY.includes(item.key));
  const activeInMore = secondary.some((item) => item.key === active);

  useEffect(() => {
    if (!moreOpen) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMoreOpen(false);
        moreButtonRef.current?.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [moreOpen]);

  return (
    <div className="relative shrink-0 md:hidden">
      {moreOpen && (
        <>
          <button
            type="button"
            aria-label="Fechar menu"
            onClick={() => setMoreOpen(false)}
            className="fixed inset-0 z-40 bg-ink/30"
          />
          <div
            id="mobile-more-menu"
            role="dialog"
            aria-label="Mais opções"
            className="absolute inset-x-0 bottom-full z-50 border-t border-nav-bg-2 bg-nav-bg px-3 py-3"
          >
            {secondary.map((item) => {
              const isActive = item.key === active;
              return (
                <Link
                  key={item.key}
                  href={item.href}
                  aria-current={isActive ? "page" : undefined}
                  onClick={() => setMoreOpen(false)}
                  className={`flex h-12 items-center gap-3.5 rounded-md px-3 text-[15px] ${
                    isActive
                      ? "bg-nav-active font-semibold text-nav-ink"
                      : "font-medium text-nav-ink-muted hover:bg-nav-bg-2 hover:text-nav-ink"
                  }`}
                >
                  <NavIcon>{item.icon}</NavIcon>
                  {item.label}
                </Link>
              );
            })}
            <form action={logout}>
              <button
                type="submit"
                className="flex h-12 w-full items-center gap-3.5 rounded-md px-3 text-left text-[15px] font-medium text-nav-ink-muted hover:bg-nav-bg-2 hover:text-nav-ink"
              >
                <NavIcon>
                  <>
                    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                    <path d="m16 17 5-5-5-5" />
                    <path d="M21 12H9" />
                  </>
                </NavIcon>
                Sair
              </button>
            </form>
          </div>
        </>
      )}

      <nav
        aria-label="Navegação principal"
        className="grid grid-cols-5 border-t border-nav-bg-2 bg-nav-bg pb-[env(safe-area-inset-bottom)]"
      >
        {primary.map((item) => {
          const isActive = item.key === active;
          return (
            <Link
              key={item.key}
              href={item.href}
              aria-current={isActive ? "page" : undefined}
              className={`flex min-h-14 flex-col items-center justify-center gap-1 text-micro ${
                isActive ? "font-semibold text-nav-ink" : "text-nav-ink-muted"
              }`}
            >
              <span className="relative">
                <NavIcon>{item.icon}</NavIcon>
                {item.key === "conversas" && <UrgentBadge count={urgentCount} />}
              </span>
              {item.label}
            </Link>
          );
        })}
        <button
          ref={moreButtonRef}
          type="button"
          onClick={() => setMoreOpen((open) => !open)}
          aria-expanded={moreOpen}
          aria-controls="mobile-more-menu"
          className={`flex min-h-14 flex-col items-center justify-center gap-1 text-micro ${
            activeInMore || moreOpen ? "font-semibold text-nav-ink" : "text-nav-ink-muted"
          }`}
        >
          <NavIcon>
            <>
              <circle cx="5" cy="12" r="1" />
              <circle cx="12" cy="12" r="1" />
              <circle cx="19" cy="12" r="1" />
            </>
          </NavIcon>
          Mais
        </button>
      </nav>
    </div>
  );
}
