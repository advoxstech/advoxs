"use client";

/** Fetch do browser via proxy autenticado; sessão expirada volta pro login. */
export async function backendFetch(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(`/api/backend/${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Sessão expirada");
  }
  return response;
}
