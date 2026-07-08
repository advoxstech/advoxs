import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE } from "@/lib/auth";

export function middleware(request: NextRequest) {
  const hasSession =
    request.cookies.has(ACCESS_TOKEN_COOKIE) || request.cookies.has(REFRESH_TOKEN_COOKIE);
  const { pathname } = request.nextUrl;

  if (pathname === "/") {
    return NextResponse.redirect(
      new URL(hasSession ? "/conversas" : "/login", request.url),
    );
  }

  if (pathname === "/login" && hasSession) {
    return NextResponse.redirect(new URL("/conversas", request.url));
  }

  if (pathname !== "/login" && !hasSession) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/",
    "/login",
    "/conversas/:path*",
    "/base-de-conhecimento/:path*",
    "/configuracoes/:path*",
  ],
};
