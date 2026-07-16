import { ConversationsPanel } from "@/components/ConversationsPanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default async function ConversasPage({
  searchParams,
}: {
  searchParams: Promise<{ aba?: string }>;
}) {
  const { aba } = await searchParams;
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="conversas" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <ConversationsPanel initialOrigin={aba === "testes" ? "test" : "real"} />
      </div>
    </div>
  );
}
