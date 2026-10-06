"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { API_URL } from "@/lib/backend";

export type VerifyState = { error: string | null };

export async function confirmEmail(_prev: VerifyState, formData: FormData): Promise<VerifyState> {
  const token = String(formData.get("token") ?? "");
  if (!token) return { error: "Link inválido. Solicite outro e-mail de confirmação." };
  try {
    const response = await fetch(`${API_URL}/api/v1/signup/verify-email`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token }),
      cache: "no-store",
    });
    if (!response.ok) {
      return { error: "Este link expirou ou já foi usado. Solicite outro e-mail de confirmação." };
    }
    const body = (await response.json()) as { checkout_token: string };
    (await cookies()).set("signup_checkout", body.checkout_token, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/cadastro",
      maxAge: 24 * 60 * 60,
    });
  } catch {
    return { error: "Não foi possível confirmar agora. Tente novamente." };
  }
  redirect("/cadastro/pagamento");
}
