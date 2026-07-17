import { CreditosExtrato } from "@/components/CreditosExtrato";
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

export default async function CreditosPage() {
  const packages = await getPackages();

  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="creditos" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <CreditosPanel packages={packages} />
        <div className="px-8 pb-8">
          <CreditosExtrato />
        </div>
      </main>
    </div>
  );
}
