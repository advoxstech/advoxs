import { TenantNav } from "@/components/TenantNav";
import { WhatsAppConnectionPanel } from "@/components/WhatsAppConnectionPanel";

export default function ConfiguracoesWhatsAppPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="config" />
      <WhatsAppConnectionPanel />
    </div>
  );
}
