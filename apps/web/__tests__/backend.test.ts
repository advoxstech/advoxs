import { describe, expect, it } from "vitest";

import { isAllowedPath } from "@/lib/backend";

describe("isAllowedPath", () => {
  it("permite rotas de conversas", () => {
    expect(isAllowedPath(["conversations"])).toBe(true);
    expect(isAllowedPath(["conversations", "abc", "messages"])).toBe(true);
  });

  it("bloqueia auth, webhooks e caminho vazio", () => {
    expect(isAllowedPath(["auth", "login"])).toBe(false);
    expect(isAllowedPath(["webhooks", "whatsapp"])).toBe(false);
    expect(isAllowedPath([])).toBe(false);
  });

  it("permite rotas de knowledge-base", () => {
    expect(isAllowedPath(["knowledge-base", "files"])).toBe(true);
  });

  it("permite rotas de whatsapp", () => {
    expect(isAllowedPath(["whatsapp", "connection"])).toBe(true);
  });

  it("permite rotas de signup", () => {
    expect(isAllowedPath(["signup", "status"])).toBe(true);
  });

  it("permite rotas de conversas de teste e onboarding", () => {
    // Regressão real de produção: prefixos fora do allowlist fazem o proxy
    // devolver 404 sem chegar no api — o botão "Nova conversa de teste"
    // não fazia nada, e o tutorial nunca aparecia (mascarado pelo fail-open).
    expect(isAllowedPath(["test-conversations"])).toBe(true);
    expect(isAllowedPath(["onboarding"])).toBe(true);
    expect(isAllowedPath(["onboarding", "complete"])).toBe(true);
  });
});
