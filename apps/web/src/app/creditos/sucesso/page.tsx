import { CreditosSucessoPanel } from "@/components/CreditosSucessoPanel";
import { TenantNav } from "@/components/TenantNav";

export default async function CreditosSucessoPage({
  searchParams,
}: {
  searchParams: Promise<{ session_id?: string }>;
}) {
  const { session_id } = await searchParams;

  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="creditos" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <CreditosSucessoPanel sessionId={session_id ?? null} />
      </main>
    </div>
  );
}
