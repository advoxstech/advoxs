import { KnowledgeBasePanel } from "@/components/KnowledgeBasePanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantShell } from "@/components/TenantShell";

export default function BaseDeConhecimentoPage() {
  return (
    <TenantShell active="base">
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <KnowledgeBasePanel />
      </div>
    </TenantShell>
  );
}
