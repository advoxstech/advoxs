"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { setAuthCookies } from "@/lib/auth";
import { API_URL } from "@/lib/backend";

export interface LoginState {
  error: string | null;
}

export async function login(_prev: LoginState, formData: FormData): Promise<LoginState> {
  const email = String(formData.get("email") ?? "");
  const password = String(formData.get("password") ?? "");

  let tokens: { access_token: string; refresh_token: string };
  try {
    const response = await fetch(`${API_URL}/api/v1/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
    });

    if (response.status === 401) {
      return { error: "E-mail ou senha incorretos." };
    }
    if (response.status === 403) {
      return { error: "Escritório suspenso. Fale com o suporte da Advoxs." };
    }
    if (!response.ok) {
      return { error: "Não foi possível entrar agora. Tente novamente." };
    }
    tokens = await response.json();
  } catch {
    return { error: "Não foi possível conectar ao servidor. Tente novamente." };
  }

  setAuthCookies(await cookies(), tokens);
  redirect("/conversas");
}
