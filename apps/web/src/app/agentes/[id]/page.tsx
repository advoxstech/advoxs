import { AgentDetail } from "@/components/AgentDetail";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantShell } from "@/components/TenantShell";

export default async function AgenteDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <TenantShell active="agentes">
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <AgentDetail key={id} agentId={id} />
      </div>
    </TenantShell>
  );
}
