import { KnowledgeBasePanel } from "@/components/KnowledgeBasePanel";
import { TenantNav } from "@/components/TenantNav";

export default function BaseDeConhecimentoPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="base" />
      <KnowledgeBasePanel />
    </div>
  );
}
