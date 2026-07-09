import { ConversationsPanel } from "@/components/ConversationsPanel";
import { TenantNav } from "@/components/TenantNav";

export default function ConversasPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="conversas" />
      <ConversationsPanel />
    </div>
  );
}
