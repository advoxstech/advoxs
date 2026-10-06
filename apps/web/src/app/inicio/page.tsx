import { DashboardPanel } from "@/components/DashboardPanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { OnboardingGate } from "@/components/OnboardingGate";
import { TenantShell } from "@/components/TenantShell";

export default function InicioPage() {
  return (
    <TenantShell active="inicio">
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <main className="flex-1 overflow-y-auto bg-ground">
          <OnboardingGate>
            <DashboardPanel />
          </OnboardingGate>
        </main>
      </div>
    </TenantShell>
  );
}
