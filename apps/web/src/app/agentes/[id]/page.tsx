import { AgentDetail } from "@/components/AgentDetail";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default async function AgenteDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="agentes" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <AgentDetail agentId={id} />
      </div>
    </div>
  );
}
