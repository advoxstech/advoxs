/** Next may see the internal container URL; Host retains the public port/domain. */
export function isSameOrigin(request: Request): boolean {
  try {
    const origin = new URL(request.headers.get("origin") ?? "");
    return ["http:", "https:"].includes(origin.protocol)
      && origin.host === request.headers.get("host");
  } catch {
    return false;
  }
}
