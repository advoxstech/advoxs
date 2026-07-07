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
});
