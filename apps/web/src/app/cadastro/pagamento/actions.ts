"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { API_URL } from "@/lib/backend";

export type PaymentState = { error: string | null };

export async function startPayment(_prev: PaymentState): Promise<PaymentState> {
  const checkout_token = (await cookies()).get("signup_checkout")?.value;
  if (!checkout_token) return { error: "Confirme seu e-mail novamente para continuar." };
  let checkoutUrl: string;
  try {
    const response = await fetch(`${API_URL}/api/v1/signup/checkout`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ checkout_token }),
      cache: "no-store",
    });
    if (!response.ok) {
      return { error: "Não foi possível iniciar o pagamento. Tente novamente ou confirme o e-mail de novo." };
    }
    checkoutUrl = (await response.json()).checkout_url;
  } catch {
    return { error: "Não foi possível conectar ao servidor. Tente novamente." };
  }
  redirect(checkoutUrl);
}
