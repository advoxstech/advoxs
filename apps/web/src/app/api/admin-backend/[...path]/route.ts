import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import {
  PLATFORM_ACCESS_TOKEN_COOKIE,
  PLATFORM_REFRESH_TOKEN_COOKIE,
  clearPlatformAuthCookies,
  setPlatformAuthCookies,
} from "@/lib/admin-auth";
import { isAdminAllowedPath } from "@/lib/admin-backend";
import { API_URL } from "@/lib/backend";

/**
 * Proxy autenticado do painel de admin — nunca reaproveita o proxy dos
 * tenants (/api/backend/*): cookies, endpoint de refresh e allowlist
 * próprios, pra sessão de admin nunca se confundir com sessão de tenant.
 */
async function handle(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const { path } = await params;
  if (!isAdminAllowedPath(path)) {
    return NextResponse.json({ detail: "Rota não permitida" }, { status: 404 });
  }

  const store = await cookies();
  const url = `${API_URL}/api/v1/${path.join("/")}${request.nextUrl.search}`;
  const hasBody = request.method !== "GET" && request.method !== "DELETE";
  const contentType = request.headers.get("content-type");
  const body = hasBody ? await request.arrayBuffer() : undefined;

  const forward = (token: string | undefined) =>
    fetch(url, {
      method: request.method,
      headers: {
        ...(hasBody && contentType ? { "content-type": contentType } : {}),
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body,
      cache: "no-store",
    });

  let response = await forward(store.get(PLATFORM_ACCESS_TOKEN_COOKIE)?.value);

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
  const refreshToken = store.get(PLATFORM_REFRESH_TOKEN_COOKIE)?.value;
  if (!refreshToken) {
    return null;
  }

  const response = await fetch(`${API_URL}/api/v1/platform-admin/auth/refresh`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
    cache: "no-store",
  });

  if (!response.ok) {
    clearPlatformAuthCookies(store);
    return null;
  }

  const tokens = await response.json();
  setPlatformAuthCookies(store, tokens);
  return tokens.access_token;
}

export { handle as GET, handle as POST, handle as PATCH, handle as DELETE };
