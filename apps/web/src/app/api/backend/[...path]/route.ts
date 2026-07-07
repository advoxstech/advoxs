import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import {
  ACCESS_TOKEN_COOKIE,
  REFRESH_TOKEN_COOKIE,
  clearAuthCookies,
  setAuthCookies,
} from "@/lib/auth";
import { API_URL, isAllowedPath } from "@/lib/backend";

/**
 * Proxy autenticado para o FastAPI. Os tokens vivem em cookies httpOnly,
 * então o JS do browser chama /api/backend/* e o token é anexado aqui.
 * Access token expirado é renovado de forma transparente (uma tentativa).
 */
async function handle(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const { path } = await params;
  if (!isAllowedPath(path)) {
    return NextResponse.json({ detail: "Rota não permitida" }, { status: 404 });
  }

  const store = await cookies();
  const url = `${API_URL}/api/v1/${path.join("/")}${request.nextUrl.search}`;
  const body = request.method === "GET" ? undefined : await request.text();

  const forward = (token: string | undefined) =>
    fetch(url, {
      method: request.method,
      headers: {
        "content-type": "application/json",
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body,
      cache: "no-store",
    });

  let response = await forward(store.get(ACCESS_TOKEN_COOKIE)?.value);

  if (response.status === 401) {
    const newAccessToken = await refreshSession(store);
    if (newAccessToken === null) {
      return NextResponse.json({ detail: "Sessão expirada" }, { status: 401 });
    }
    response = await forward(newAccessToken);
  }

  const payload = await response.text();
  return new NextResponse(payload, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
    },
  });
}

async function refreshSession(
  store: Awaited<ReturnType<typeof cookies>>,
): Promise<string | null> {
  const refreshToken = store.get(REFRESH_TOKEN_COOKIE)?.value;
  if (!refreshToken) {
    return null;
  }

  const response = await fetch(`${API_URL}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
    cache: "no-store",
  });

  if (!response.ok) {
    clearAuthCookies(store);
    return null;
  }

  const tokens = await response.json();
  setAuthCookies(store, tokens);
  return tokens.access_token;
}

export { handle as GET, handle as POST, handle as PATCH };
