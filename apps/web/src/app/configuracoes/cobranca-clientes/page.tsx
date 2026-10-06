import { EndCustomerBillingTabs } from "@/components/EndCustomerBillingTabs";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantShell } from "@/components/TenantShell";

export default function ConfiguracoesCobrancaClientesPage() {
  return (
    <TenantShell active="cobranca">
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <EndCustomerBillingTabs />
      </div>
    </TenantShell>
  );
}
