import { TeamPanel } from "@/components/TeamPanel";
import { TenantShell } from "@/components/TenantShell";

export default function Page() {
  return (
    <TenantShell active="equipe">
      <TeamPanel />
    </TenantShell>
  );
}
