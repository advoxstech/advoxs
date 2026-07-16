"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { setAuthCookies } from "@/lib/auth";
import { API_URL } from "@/lib/backend";

export async function autoLogin(token: string): Promise<{ error: string | null }> {
  let tokens: { access_token: string; refresh_token: string };
  try {
    const response = await fetch(`${API_URL}/api/v1/auth/signup-login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token }),
      cache: "no-store",
    });
    if (!response.ok) {
      return { error: "invalid" };
    }
    tokens = await response.json();
  } catch {
    return { error: "network" };
  }

  setAuthCookies(await cookies(), tokens);
  // Fora do try/catch: o redirect do Next lança NEXT_REDIRECT por design.
  redirect("/inicio");
}
