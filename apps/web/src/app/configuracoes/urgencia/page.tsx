import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";
import { UrgencyKeywordsPanel } from "@/components/UrgencyKeywordsPanel";

export default function ConfiguracoesUrgenciaPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="urgencia" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <UrgencyKeywordsPanel />
      </div>
    </div>
  );
}
