import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  redirect: vi.fn(),
}));

// `cookies()` do next/headers exige um request scope real do Next.js — em
// unit test (vitest puro, sem servidor Next rodando) precisa ser mockado.
vi.mock("next/headers", () => ({
  cookies: vi.fn(async () => ({
    get: vi.fn(),
    set: vi.fn(),
    delete: vi.fn(),
  })),
}));

import { redirect } from "next/navigation";

import { adminLogin } from "@/app/admin/actions";

const mockedRedirect = redirect as ReturnType<typeof vi.fn>;
const mockedFetch = vi.fn();

beforeEach(() => {
  mockedRedirect.mockReset();
  mockedFetch.mockReset();
  vi.stubGlobal("fetch", mockedFetch);
});

function formData(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) data.append(key, value);
  return data;
}

describe("adminLogin action", () => {
  it("redireciona para /admin em caso de sucesso", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ access_token: "a", refresh_token: "b" }),
    });

    await adminLogin({ error: null }, formData({ email: "a@b.com", password: "senha123" }));

    expect(mockedRedirect).toHaveBeenCalledWith("/admin");
  });

  it("retorna erro claro em credenciais inválidas (401)", async () => {
    mockedFetch.mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });

    const result = await adminLogin(
      { error: null },
      formData({ email: "a@b.com", password: "errada" }),
    );

    expect(result.error).toBe("E-mail ou senha incorretos.");
    expect(mockedRedirect).not.toHaveBeenCalled();
  });

  it("trata falha de rede", async () => {
    mockedFetch.mockRejectedValue(new Error("network down"));

    const result = await adminLogin({ error: null }, formData({ email: "a@b.com", password: "x" }));

    expect(result.error).toBe("Não foi possível conectar ao servidor. Tente novamente.");
  });
});
