import { DashboardPanel } from "@/components/DashboardPanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { OnboardingGate } from "@/components/OnboardingGate";
import { TenantNav } from "@/components/TenantNav";

export default function InicioPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="inicio" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <main className="flex-1 overflow-y-auto bg-ground">
          <OnboardingGate>
            <DashboardPanel />
          </OnboardingGate>
        </main>
      </div>
    </div>
  );
}
