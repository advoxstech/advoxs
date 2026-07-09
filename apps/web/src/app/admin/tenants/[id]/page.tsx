import Link from "next/link";

import { AdminTenantDetail } from "@/components/AdminTenantDetail";

import { adminLogout } from "../../actions";

export default async function AdminTenantDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <div className="flex h-screen overflow-hidden">
      <nav className="flex w-14 shrink-0 flex-col items-center justify-between bg-ink py-5">
        <div className="flex flex-col items-center gap-6">
          <span className="font-display text-2xl font-semibold text-ground" aria-label="Admin">
            A.
          </span>
          <Link
            href="/admin"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Dashboard
          </Link>
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
        <AdminTenantDetail tenantId={id} />
      </main>
    </div>
  );
}
