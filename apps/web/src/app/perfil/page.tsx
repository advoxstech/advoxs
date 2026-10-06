import { ProfilePanel } from "@/components/ProfilePanel";
import { TenantShell } from "@/components/TenantShell";

export default function PerfilPage() {
  return (
    <TenantShell active="perfil">
      <main className="flex-1 overflow-y-auto bg-ground">
        <ProfilePanel />
      </main>
    </TenantShell>
  );
}
