import { CreditosPanel } from "@/components/CreditosPanel";
import { TenantNav } from "@/components/TenantNav";
import { API_URL } from "@/lib/backend";
import type { CreditPackage } from "@/lib/types";

async function getPackages(): Promise<CreditPackage[]> {
  try {
    const response = await fetch(`${API_URL}/api/v1/credit-packages`, { cache: "no-store" });
    if (!response.ok) return [];
    return response.json();
  } catch {
    return [];
  }
}

export default async function CreditosPage({
  searchParams,
}: {
  searchParams: Promise<{ session_id?: string }>;
}) {
  const [packages, { session_id }] = await Promise.all([getPackages(), searchParams]);

  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="creditos" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <CreditosPanel packages={packages} sessionId={session_id ?? null} />
      </main>
    </div>
  );
}
