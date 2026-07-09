import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  redirect: vi.fn(),
}));

import { redirect } from "next/navigation";

import { signup } from "@/app/actions";

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

describe("signup action", () => {
  it("redireciona para o checkout_url em caso de sucesso", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ checkout_url: "https://checkout.stripe.com/pay/cs_123" }),
    });

    await signup(
      { error: null },
      formData({
        tenant_name: "Escritório Teste",
        email: "a@b.com",
        password: "senha1234",
        credit_package_id: "pkg-1",
      }),
    );

    expect(mockedRedirect).toHaveBeenCalledWith("https://checkout.stripe.com/pay/cs_123");
  });

  it("retorna a mensagem de erro (string) quando a API rejeita", async () => {
    mockedFetch.mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "Este e-mail já está cadastrado" }),
    });

    const result = await signup({ error: null }, formData({ email: "a@b.com" }));

    expect(result.error).toBe("Este e-mail já está cadastrado");
    expect(mockedRedirect).not.toHaveBeenCalled();
  });

  it("usa mensagem padrão quando detail não é string (ex: erro 422 em array)", async () => {
    mockedFetch.mockResolvedValue({
      ok: false,
      json: async () => ({
        detail: [{ type: "string_too_short", loc: ["body", "password"] }],
      }),
    });

    const result = await signup({ error: null }, formData({ email: "a@b.com" }));

    expect(result.error).toBe("Não foi possível iniciar o pagamento. Tente novamente.");
  });

  it("trata falha de rede", async () => {
    mockedFetch.mockRejectedValue(new Error("network down"));

    const result = await signup({ error: null }, formData({ email: "a@b.com" }));

    expect(result.error).toBe("Não foi possível conectar ao servidor. Tente novamente.");
  });
});
