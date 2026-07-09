import { KnowledgeBasePanel } from "@/components/KnowledgeBasePanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default function BaseDeConhecimentoPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="base" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <KnowledgeBasePanel />
      </div>
    </div>
  );
}
