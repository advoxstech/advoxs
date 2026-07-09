import Link from "next/link";

import { AdminDashboardPanel } from "@/components/AdminDashboardPanel";

import { adminLogout } from "./actions";

export default function AdminDashboardPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <nav className="flex w-14 shrink-0 flex-col items-center justify-between bg-ink py-5">
        <div className="flex flex-col items-center gap-6">
          <span className="font-display text-2xl font-semibold text-ground" aria-label="Admin">
            A.
          </span>
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground [writing-mode:vertical-rl]">
            Dashboard
          </span>
          <Link
            href="/admin/tenants"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Tenants
          </Link>
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
      <main className="flex-1 overflow-y-auto bg-ground">
        <AdminDashboardPanel />
      </main>
    </div>
  );
}
