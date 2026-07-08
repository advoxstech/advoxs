/** Base do FastAPI para chamadas server-side (proxy, actions, middleware). */
export const API_URL =
  process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const ALLOWED_PREFIXES = ["conversations", "knowledge-base", "whatsapp"];

/** Só rotas do painel passam pelo proxy — nunca auth ou webhooks. */
export function isAllowedPath(path: string[]): boolean {
  const [first] = path;
  return first !== undefined && ALLOWED_PREFIXES.includes(first);
}
