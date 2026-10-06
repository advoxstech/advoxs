"use server";

import { redirect } from "next/navigation";

import { API_URL } from "@/lib/backend";

export interface SignupState {
  error: string | null;
}

export async function signup(_prev: SignupState, formData: FormData): Promise<SignupState> {
  const tenant_name = String(formData.get("tenant_name") ?? "");
  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");
  const credit_package_id = String(formData.get("credit_package_id") ?? "");

  try {
    const response = await fetch(`${API_URL}/api/v1/signup/request-verification`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ tenant_name, email, password, credit_package_id }),
      cache: "no-store",
    });

    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const detail = typeof body?.detail === "string" ? body.detail : null;
      return { error: detail ?? "Não foi possível enviar a confirmação. Tente novamente." };
    }
  } catch {
    return { error: "Não foi possível conectar ao servidor. Tente novamente." };
  }

  redirect("/cadastro/verifique");
}
