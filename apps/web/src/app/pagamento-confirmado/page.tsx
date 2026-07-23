import { PagamentoConfirmadoContent } from "@/components/PagamentoConfirmadoContent";

export default async function PagamentoConfirmadoPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const { status } = await searchParams;

  return <PagamentoConfirmadoContent status={status} />;
}
