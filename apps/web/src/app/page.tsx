import { SignupAside } from "@/components/SignupAside";
import { SignupForm } from "@/components/SignupForm";
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

export default async function HomePage() {
  const packages = await getPackages();

  return (
    <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[0.85fr_1fr]">
      <SignupAside />
      <main className="flex justify-center px-6 py-14 lg:px-16">
        {packages.length > 0 ? (
          <SignupForm packages={packages} />
        ) : (
          <p className="h-fit w-full max-w-[520px] rounded-sm border border-line bg-surface px-4 py-3 text-sm text-muted">
            Não foi possível carregar os planos agora. Tente recarregar a página em instantes.
          </p>
        )}
      </main>
    </div>
  );
}
