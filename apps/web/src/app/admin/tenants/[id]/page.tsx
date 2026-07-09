import { AdminNav } from "@/components/AdminNav";
import { AdminTenantDetail } from "@/components/AdminTenantDetail";

export default async function AdminTenantDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <div className="flex h-screen overflow-hidden">
      <AdminNav active="tenants" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <AdminTenantDetail tenantId={id} />
      </main>
    </div>
  );
}
