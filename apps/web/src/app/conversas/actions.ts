"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { clearAuthCookies, REFRESH_TOKEN_COOKIE } from "@/lib/auth";
import { API_URL } from "@/lib/backend";

export async function logout(): Promise<void> {
  const store = await cookies();
  const refreshToken = store.get(REFRESH_TOKEN_COOKIE)?.value;

  if (refreshToken) {
    // Revogação no servidor é melhor esforço — a sessão local sempre encerra.
    try {
      await fetch(`${API_URL}/api/v1/auth/logout`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
        cache: "no-store",
      });
    } catch {
      // segue o fluxo local
    }
  }

  clearAuthCookies(store);
  redirect("/login");
}
