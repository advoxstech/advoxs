import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { cookieDelete } = vi.hoisted(() => ({ cookieDelete: vi.fn() }));

vi.mock("next/headers", () => ({
  cookies: vi.fn(async () => ({
    get: vi.fn((name: string) => (name === "access_token" ? { value: "token-valido" } : undefined)),
    delete: cookieDelete,
  })),
}));

import { GET, POST } from "@/app/api/backend/[...path]/route";

const mockedFetch = vi.fn();

beforeEach(() => {
  mockedFetch.mockReset();
  cookieDelete.mockReset();
  vi.stubGlobal("fetch", mockedFetch);
});

describe("proxy após troca de senha", () => {
  it("remove os cookies quando a senha é alterada", async () => {
    mockedFetch.mockResolvedValue({ status: 204, headers: new Headers() });
    const request = new NextRequest("http://localhost:3000/api/backend/profile/password", {
      method: "POST",
      body: JSON.stringify({ current_password: "atual", new_password: "nova12345" }),
      headers: { "content-type": "application/json" },
    });

    const response = await POST(request, {
      params: Promise.resolve({ path: ["profile", "password"] }),
    });

    expect(response.status).toBe(204);
    expect(cookieDelete).toHaveBeenCalledWith("access_token");
    expect(cookieDelete).toHaveBeenCalledWith("refresh_token");
  });
});

// NextRequest (não Request puro) — a rota lê `request.nextUrl`, que só existe
// nessa subclasse do fetch API padrão usada pelos route handlers do Next.js.
function makeRequest(path: string): NextRequest {
  return new NextRequest(`http://localhost:3000/api/backend/${path}`, { method: "GET" });
}

describe("proxy binário", () => {
  it("repassa bytes não-UTF-8 sem corromper (ex: PNG)", async () => {
    // Um PNG começa com esses 8 bytes de assinatura — não é UTF-8 válido.
    const pngBytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0xff, 0xd8]);
    mockedFetch.mockResolvedValue({
      status: 200,
      headers: new Headers({ "content-type": "image/png" }),
      arrayBuffer: async () => pngBytes.buffer,
    });

    const response = await GET(makeRequest("profile/logo"), {
      params: Promise.resolve({ path: ["profile", "logo"] }),
    });

    const received = new Uint8Array(await response.arrayBuffer());
    expect(Array.from(received)).toEqual(Array.from(pngBytes));
    expect(response.headers.get("content-type")).toBe("image/png");
  });

  it("repassa 204 sem corpo (Response proíbe payload em 204)", async () => {
    // Regressão real de produção: new NextResponse(arrayBuffer, {status: 204})
    // lança TypeError — quebrava heartbeat, DELETE de conversa de teste e
    // onboarding/complete, virando 500 pro cliente.
    mockedFetch.mockResolvedValue({
      status: 204,
      headers: new Headers(),
      arrayBuffer: async () => new ArrayBuffer(0),
    });

    const response = await GET(makeRequest("conversations/c1/heartbeat"), {
      params: Promise.resolve({ path: ["conversations", "c1", "heartbeat"] }),
    });

    expect(response.status).toBe(204);
    expect(response.body).toBeNull();
  });
});
