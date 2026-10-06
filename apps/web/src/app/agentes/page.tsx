import { AgentsPanel } from "@/components/AgentsPanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantShell } from "@/components/TenantShell";

export default function AgentesPage() {
  return (
    <TenantShell active="agentes">
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <AgentsPanel />
      </div>
    </TenantShell>
  );
}
