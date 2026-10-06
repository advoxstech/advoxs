"use server";

import { API_URL } from "@/lib/backend";

export type ResendState = { error: string | null; sent: boolean };

export async function resendVerification(
  _prev: ResendState,
  formData: FormData,
): Promise<ResendState> {
  const email = String(formData.get("email") ?? "");
  try {
    const response = await fetch(`${API_URL}/api/v1/signup/resend-verification`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email }),
      cache: "no-store",
    });
    if (response.status === 429) {
      const body = await response.json().catch(() => null);
      return { error: body?.detail ?? "Aguarde antes de solicitar outro envio.", sent: false };
    }
    if (!response.ok) {
      return { error: "Não foi possível reenviar agora. Tente novamente.", sent: false };
    }
    return { error: null, sent: true };
  } catch {
    return { error: "Não foi possível conectar ao servidor. Tente novamente.", sent: false };
  }
}
