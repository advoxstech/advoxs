import { SignupSuccessPanel } from "@/components/SignupSuccessPanel";

export default async function CadastroSucessoPage({
  searchParams,
}: {
  searchParams: Promise<{ session_id?: string }>;
}) {
  const { session_id } = await searchParams;
  return <SignupSuccessPanel sessionId={session_id ?? null} />;
}
