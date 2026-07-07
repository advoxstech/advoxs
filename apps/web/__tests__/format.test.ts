import { describe, expect, it } from "vitest";

import { formatMessageTime, formatPhone } from "@/lib/format";

describe("formatPhone", () => {
  it("formata número brasileiro com 9 dígitos", () => {
    expect(formatPhone("5511999998888")).toBe("+55 11 99999-8888");
  });

  it("formata número brasileiro com 8 dígitos", () => {
    expect(formatPhone("551133334444")).toBe("+55 11 3333-4444");
  });

  it("devolve o valor original quando não reconhece o formato", () => {
    expect(formatPhone("123")).toBe("123");
  });
});

describe("formatMessageTime", () => {
  it("mostra só a hora para mensagens de hoje", () => {
    const now = new Date("2026-07-07T15:00:00");
    expect(formatMessageTime("2026-07-07T14:30:00", now)).toBe("14:30");
  });

  it("mostra data e hora para mensagens de outros dias", () => {
    const now = new Date("2026-07-07T15:00:00");
    expect(formatMessageTime("2026-07-01T09:05:00", now)).toBe("01/07 09:05");
  });
});
