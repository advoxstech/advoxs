import { describe, expect, it } from "vitest";

import { isAdminAllowedPath } from "@/lib/admin-backend";

describe("isAdminAllowedPath", () => {
  it("permite rotas de platform-admin", () => {
    expect(isAdminAllowedPath(["platform-admin", "dashboard"])).toBe(true);
    expect(isAdminAllowedPath(["platform-admin", "tenants", "abc"])).toBe(true);
  });

  it("bloqueia rotas de tenant e caminho vazio", () => {
    expect(isAdminAllowedPath(["conversations"])).toBe(false);
    expect(isAdminAllowedPath(["knowledge-base", "files"])).toBe(false);
    expect(isAdminAllowedPath([])).toBe(false);
  });
});
