import { redirect } from "next/navigation";

// O middleware decide entre /login e /conversas; isto cobre acesso direto.
export default function HomePage() {
  redirect("/conversas");
}
