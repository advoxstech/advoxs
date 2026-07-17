import { describe, expect, it } from "vitest";

import { formatCredits, formatMessageTime, formatPhone } from "@/lib/format";

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

describe("formatCredits", () => {
  it("usa separador de milhar pt-BR para valores inteiros", () => {
    expect(formatCredits(1500)).toBe("1.500");
  });

  it("mostra até 2 casas decimais para créditos fracionados", () => {
    expect(formatCredits(1.75)).toBe("1,75");
  });

  it("arredonda para no máximo 2 casas decimais na exibição", () => {
    expect(formatCredits(1.7549)).toBe("1,75");
  });

  it("formata zero normalmente", () => {
    expect(formatCredits(0)).toBe("0");
  });
});
