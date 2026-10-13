"use client";

/** Fetch do browser via proxy autenticado; sessão expirada volta pro login. */
export async function backendFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const isFormData = init?.body instanceof FormData;
  const response = await fetch(`/api/backend/${path}`, {
    ...init,
    headers: {
      ...(isFormData ? {} : { "content-type": "application/json" }),
      ...init?.headers,
    },
  });
  if (response.status === 401) {
    window.location.href =
      "/login?next=" +
      encodeURIComponent(window.location.pathname + window.location.search);
    throw new Error("Sessão expirada");
  }
  return response;
}
