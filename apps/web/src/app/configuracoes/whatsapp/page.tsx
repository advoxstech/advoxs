import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";
import { WhatsAppConnectionPanel } from "@/components/WhatsAppConnectionPanel";

export default function ConfiguracoesWhatsAppPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="config" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <WhatsAppConnectionPanel />
      </div>
    </div>
  );
}
