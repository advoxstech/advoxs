import { AgentsPanel } from "@/components/AgentsPanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default function AgentesPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="agentes" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <AgentsPanel />
      </div>
    </div>
  );
}
