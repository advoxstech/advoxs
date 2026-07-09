const ALLOWED_PREFIXES = ["platform-admin"];

/** Só rotas do painel de admin passam por este proxy — nunca as de tenant. */
export function isAdminAllowedPath(path: string[]): boolean {
  const [first] = path;
  return first !== undefined && ALLOWED_PREFIXES.includes(first);
}
