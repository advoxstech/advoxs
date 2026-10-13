import { cookies } from "next/headers";
import { ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE, setAuthCookies } from "./auth";
import { API_URL } from "./backend";

/** Identity endpoints also work before the user selects an office. */
export async function identityFetch(path: string, init?: RequestInit) {
  const store = await cookies();
  const forward = (token?: string) => fetch(`${API_URL}/api/v1/${path}`, {
    ...init, cache: "no-store",
    headers: { "content-type": "application/json", ...init?.headers,
      ...(token ? { authorization: `Bearer ${token}` } : {}) },
  });
  let response = await forward(store.get(ACCESS_TOKEN_COOKIE)?.value);
  if ([401, 409].includes(response.status) && store.get(REFRESH_TOKEN_COOKIE)?.value) {
    const refresh = await fetch(`${API_URL}/api/v1/auth/refresh`, {
      method: "POST", cache: "no-store", headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: store.get(REFRESH_TOKEN_COOKIE)?.value }),
    });
    if (refresh.ok) {
      const tokens = await refresh.json();
      setAuthCookies(store, tokens);
      response = await forward(tokens.access_token);
    }
  }
  return response;
}
