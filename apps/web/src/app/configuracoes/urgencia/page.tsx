import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantShell } from "@/components/TenantShell";
import { UrgencyKeywordsPanel } from "@/components/UrgencyKeywordsPanel";

export default function ConfiguracoesUrgenciaPage() {
  return (
    <TenantShell active="urgencia">
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <UrgencyKeywordsPanel />
      </div>
    </TenantShell>
  );
}
