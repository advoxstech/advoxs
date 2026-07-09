import { AdminNav } from "@/components/AdminNav";
import { AdminTenantsList } from "@/components/AdminTenantsList";

export default function AdminTenantsPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <AdminNav active="tenants" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <AdminTenantsList />
      </main>
    </div>
  );
}
