/** Cookies httpOnly com os tokens do platform_admin — isolados dos cookies de tenant. */

export const PLATFORM_ACCESS_TOKEN_COOKIE = "platform_access_token";
export const PLATFORM_REFRESH_TOKEN_COOKIE = "platform_refresh_token";

const ACCESS_MAX_AGE_SECONDS = 15 * 60;
const REFRESH_MAX_AGE_SECONDS = 30 * 24 * 60 * 60;

interface CookieSetter {
  set(name: string, value: string, options: Record<string, unknown>): void;
  delete(name: string): void;
}

function baseOptions() {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
  };
}

export function setPlatformAuthCookies(
  store: CookieSetter,
  tokens: { access_token: string; refresh_token: string },
): void {
  store.set(PLATFORM_ACCESS_TOKEN_COOKIE, tokens.access_token, {
    ...baseOptions(),
    maxAge: ACCESS_MAX_AGE_SECONDS,
  });
  store.set(PLATFORM_REFRESH_TOKEN_COOKIE, tokens.refresh_token, {
    ...baseOptions(),
    maxAge: REFRESH_MAX_AGE_SECONDS,
  });
}

export function clearPlatformAuthCookies(store: CookieSetter): void {
  store.delete(PLATFORM_ACCESS_TOKEN_COOKIE);
  store.delete(PLATFORM_REFRESH_TOKEN_COOKIE);
}
