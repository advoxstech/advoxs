import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantShell } from "@/components/TenantShell";
import { WhatsAppConnectionPanel } from "@/components/WhatsAppConnectionPanel";

export default function ConfiguracoesWhatsAppPage() {
  return (
    <TenantShell active="config">
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <WhatsAppConnectionPanel />
      </div>
    </TenantShell>
  );
}
