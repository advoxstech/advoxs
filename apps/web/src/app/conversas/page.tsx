import { ConversationsPanel } from "@/components/ConversationsPanel";
import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { TenantNav } from "@/components/TenantNav";

export default function ConversasPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="conversas" />
      <div className="flex flex-1 flex-col overflow-hidden">
        <LowBalanceBanner />
        <ConversationsPanel />
      </div>
    </div>
  );
}
