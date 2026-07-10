import { ProfilePanel } from "@/components/ProfilePanel";
import { TenantNav } from "@/components/TenantNav";

export default function PerfilPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <TenantNav active="perfil" />
      <main className="flex-1 overflow-y-auto bg-ground">
        <ProfilePanel />
      </main>
    </div>
  );
}
