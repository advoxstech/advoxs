import { ConversationsPanel } from "@/components/ConversationsPanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantShell } from "@/components/TenantShell";

export default async function ConversasPage({
  searchParams,
}: {
  searchParams: Promise<{ aba?: string; urgentes?: string }>;
}) {
  const { aba, urgentes } = await searchParams;
  return (
    <TenantShell active="conversas">
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <ConversationsPanel
          initialOrigin={aba === "testes" ? "test" : "real"}
          initialUrgentOnly={urgentes === "1"}
        />
      </div>
    </TenantShell>
  );
}
