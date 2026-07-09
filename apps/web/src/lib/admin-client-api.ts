"use client";

/** Fetch do browser via proxy autenticado do admin; sessão expirada volta pro /admin/login. */
export async function adminBackendFetch(path: string, init?: RequestInit): Promise<Response> {
  const isFormData = init?.body instanceof FormData;
  const response = await fetch(`/api/admin-backend/${path}`, {
    ...init,
    headers: {
      ...(isFormData ? {} : { "content-type": "application/json" }),
      ...init?.headers,
    },
  });
  if (response.status === 401) {
    window.location.href = "/admin/login";
    throw new Error("Sessão expirada");
  }
  return response;
}
