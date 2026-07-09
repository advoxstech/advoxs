import { AdminNav } from "@/components/AdminNav";
import { AdminPlaygroundPanel } from "@/components/AdminPlaygroundPanel";

export default function AdminPlaygroundPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <AdminNav active="playground" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <AdminPlaygroundPanel />
      </main>
    </div>
  );
}
