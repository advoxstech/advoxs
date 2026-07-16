import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { PLATFORM_ACCESS_TOKEN_COOKIE, PLATFORM_REFRESH_TOKEN_COOKIE } from "@/lib/admin-auth";
import { ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE } from "@/lib/auth";

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (pathname.startsWith("/admin")) {
    const hasPlatformSession =
      request.cookies.has(PLATFORM_ACCESS_TOKEN_COOKIE) ||
      request.cookies.has(PLATFORM_REFRESH_TOKEN_COOKIE);

    if (pathname === "/admin/login") {
      if (hasPlatformSession) {
        return NextResponse.redirect(new URL("/admin", request.url));
      }
      return NextResponse.next();
    }

    if (!hasPlatformSession) {
      return NextResponse.redirect(new URL("/admin/login", request.url));
    }
    return NextResponse.next();
  }

  const hasSession =
    request.cookies.has(ACCESS_TOKEN_COOKIE) || request.cookies.has(REFRESH_TOKEN_COOKIE);

  if (pathname === "/") {
    if (hasSession) {
      return NextResponse.redirect(new URL("/inicio", request.url));
    }
    return NextResponse.next();
  }

  if (pathname === "/login" && hasSession) {
    return NextResponse.redirect(new URL("/inicio", request.url));
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
    "/inicio/:path*",
    "/boas-vindas/:path*",
    "/conversas/:path*",
    "/base-de-conhecimento/:path*",
    "/configuracoes/:path*",
    "/creditos/:path*",
    "/perfil/:path*",
    "/admin/:path*",
  ],
};
