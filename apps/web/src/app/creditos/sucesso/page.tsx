import { CreditosSucessoPanel } from "@/components/CreditosSucessoPanel";
import { TenantShell } from "@/components/TenantShell";

export default async function CreditosSucessoPage({
  searchParams,
}: {
  searchParams: Promise<{ session_id?: string }>;
}) {
  const { session_id } = await searchParams;

  return (
    <TenantShell active="creditos">
      <main className="flex-1 overflow-y-auto bg-ground">
        <CreditosSucessoPanel sessionId={session_id ?? null} />
      </main>
    </TenantShell>
  );
}
