import { AdminDashboardPanel } from "@/components/AdminDashboardPanel";
import { AdminNav } from "@/components/AdminNav";

export default function AdminDashboardPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <AdminNav active="dashboard" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <AdminDashboardPanel />
      </main>
    </div>
  );
}
