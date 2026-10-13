"use client";

let officeContext: string | null = null;
export function setOfficeContext(id: string) { officeContext = id; }

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
      ...(officeContext ? { "x-office-id": officeContext } : {}),
      ...init?.headers,
    },
  });
  if (response.status === 401) {
    window.location.href =
      "/login?next=" +
      encodeURIComponent(window.location.pathname + window.location.search);
    throw new Error("Sessão expirada");
  }
  if (response.status === 409) {
    const body = await response.clone().json().catch(() => ({}));
    if (["office_context_changed", "office_selection_required"].includes(body.detail)) {
      window.location.assign("/escritorios");
      throw new Error("Selecione o escritório para continuar.");
    }
  }
  return response;
}
