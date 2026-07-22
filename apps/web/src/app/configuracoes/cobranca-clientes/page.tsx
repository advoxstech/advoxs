import { EndCustomerBillingTabs } from "@/components/EndCustomerBillingTabs";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default function ConfiguracoesCobrancaClientesPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="cobranca" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <EndCustomerBillingTabs />
      </div>
    </div>
  );
}
