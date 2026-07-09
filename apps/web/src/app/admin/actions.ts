"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { clearPlatformAuthCookies, PLATFORM_REFRESH_TOKEN_COOKIE, setPlatformAuthCookies } from "@/lib/admin-auth";
import { API_URL } from "@/lib/backend";

export interface AdminLoginState {
  error: string | null;
}

export async function adminLogin(
  _prev: AdminLoginState,
  formData: FormData,
): Promise<AdminLoginState> {
  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");

  let tokens: { access_token: string; refresh_token: string };
  try {
    const response = await fetch(`${API_URL}/api/v1/platform-admin/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
    });

    if (response.status === 401) {
      return { error: "E-mail ou senha incorretos." };
    }
    if (!response.ok) {
      return { error: "Não foi possível entrar agora. Tente novamente." };
    }
    tokens = await response.json();
  } catch {
    return { error: "Não foi possível conectar ao servidor. Tente novamente." };
  }

  setPlatformAuthCookies(await cookies(), tokens);
  redirect("/admin");
}

export async function adminLogout(): Promise<void> {
  const store = await cookies();
  const refreshToken = store.get(PLATFORM_REFRESH_TOKEN_COOKIE)?.value;

  if (refreshToken) {
    // Revogação no servidor é melhor esforço — a sessão local sempre encerra.
    try {
      await fetch(`${API_URL}/api/v1/platform-admin/auth/logout`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
        cache: "no-store",
      });
    } catch {
      // segue o fluxo local
    }
  }

  clearPlatformAuthCookies(store);
  redirect("/admin/login");
}
