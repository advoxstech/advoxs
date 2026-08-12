import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WhatsAppConnectionPanel } from "@/components/WhatsAppConnectionPanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedBackendFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedBackendFetch.mockReset();
});

describe("WhatsAppConnectionPanel", () => {
  it("mostra o seletor de provedor quando não há conexão, e o formulário da Meta ao escolher", async () => {
    mockedBackendFetch.mockResolvedValue({ ok: true, json: async () => null });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /whatsapp business oficial/i })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /whatsapp business oficial/i }));

    expect(screen.getByText("Phone Number ID")).toBeInTheDocument();
  });

  it("mostra o seletor de provedor quando não há conexão", async () => {
    mockedBackendFetch.mockImplementation(async (path: string) => {
      if (path === "whatsapp/connection") return { ok: true, json: async () => null };
      if (path === "whatsapp/webhook-config") return { ok: false, json: async () => null };
      return { ok: false, json: async () => null };
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /z-api/i })).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /whatsapp business oficial/i })).toBeInTheDocument();
  });

  it("mostra o formulário Z-API e conecta com sucesso, exibindo o QR code", async () => {
    mockedBackendFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "whatsapp/connection") return { ok: true, json: async () => null };
      if (path === "whatsapp/webhook-config") return { ok: false, json: async () => null };
      if (path === "whatsapp/connect-zapi" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            provider: "zapi",
            display_phone_number: "Aguardando pareamento",
            status: "disconnected",
            connected_at: "2026-07-29T12:00:00Z",
          }),
        };
      }
      if (path === "whatsapp/zapi-qrcode") {
        return { ok: true, json: async () => ({ qrcode_base64: "data:image/png;base64,AAAA" }) };
      }
      return { ok: false, json: async () => null };
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByRole("button", { name: /z-api/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /z-api/i }));

    fireEvent.change(screen.getByLabelText(/instance id/i), { target: { value: "inst-123" } });
    fireEvent.change(screen.getByLabelText(/^token$/i), { target: { value: "token-abc" } });
    fireEvent.click(screen.getByRole("button", { name: /conectar/i }));

    await waitFor(() => expect(screen.getByAltText(/qr code/i)).toBeInTheDocument());
  });

  it("mostra o manual de instruções da Z-API ao escolher esse provedor, e não o da Meta", async () => {
    mockedBackendFetch.mockImplementation(async (path: string) => {
      if (path === "whatsapp/connection") return { ok: true, json: async () => null };
      if (path === "whatsapp/webhook-config") {
        return {
          ok: true,
          json: async () => ({
            callback_url: "https://api.exemplo.com.br/api/v1/webhooks/whatsapp",
            verify_token: "verify-abc",
          }),
        };
      }
      return { ok: false, json: async () => null };
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByText(/conectar o whatsapp business/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /z-api/i }));

    expect(screen.getByText(/conectar via z-api/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /app\.z-api\.io/i })).toHaveAttribute(
      "href",
      "https://app.z-api.io/app/auth/new-account",
    );
    expect(screen.queryByText(/conectar o whatsapp business/i)).not.toBeInTheDocument();
  });

  it("mostra aviso quando connect-zapi funciona mas o QR code falha", async () => {
    mockedBackendFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "whatsapp/connection") return { ok: true, json: async () => null };
      if (path === "whatsapp/webhook-config") return { ok: false, json: async () => null };
      if (path === "whatsapp/connect-zapi" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            provider: "zapi",
            display_phone_number: "Aguardando pareamento",
            status: "disconnected",
            connected_at: "2026-07-29T12:00:00Z",
          }),
        };
      }
      if (path === "whatsapp/zapi-qrcode") {
        return { ok: false, json: async () => null };
      }
      return { ok: false, json: async () => null };
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByRole("button", { name: /z-api/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /z-api/i }));

    fireEvent.change(screen.getByLabelText(/instance id/i), { target: { value: "inst-123" } });
    fireEvent.change(screen.getByLabelText(/^token$/i), { target: { value: "token-abc" } });
    fireEvent.click(screen.getByRole("button", { name: /conectar/i }));

    await waitFor(() =>
      expect(screen.getByText(/falha ao gerar o qr code/i)).toBeInTheDocument(),
    );
  });

  it("quando connect-zapi já retorna status connected, não busca QR code e mostra o resumo direto", async () => {
    const qrcodeFetch = vi.fn();
    mockedBackendFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "whatsapp/connection") return { ok: true, json: async () => null };
      if (path === "whatsapp/webhook-config") return { ok: false, json: async () => null };
      if (path === "whatsapp/connect-zapi" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            provider: "zapi",
            display_phone_number: "5511999998888",
            status: "connected",
            connected_at: "2026-07-29T12:00:00Z",
          }),
        };
      }
      if (path === "whatsapp/zapi-qrcode") {
        qrcodeFetch();
        return { ok: true, json: async () => ({ qrcode_base64: "data:image/png;base64,AAAA" }) };
      }
      return { ok: false, json: async () => null };
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByRole("button", { name: /z-api/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /z-api/i }));

    fireEvent.change(screen.getByLabelText(/instance id/i), { target: { value: "inst-123" } });
    fireEvent.change(screen.getByLabelText(/^token$/i), { target: { value: "token-abc" } });
    fireEvent.click(screen.getByRole("button", { name: /conectar/i }));

    await waitFor(() => expect(screen.getByText(/conectado via z-api/i)).toBeInTheDocument());
    expect(qrcodeFetch).not.toHaveBeenCalled();
  });

  it("mostra 'Conectado via Z-API' quando o provider é zapi e já está conectado", async () => {
    mockedBackendFetch.mockImplementation(async (path: string) => {
      if (path === "whatsapp/connection") {
        return {
          ok: true,
          json: async () => ({
            provider: "zapi",
            display_phone_number: "5511999998888",
            status: "connected",
            connected_at: "2026-07-29T12:00:00Z",
          }),
        };
      }
      if (path === "whatsapp/webhook-config") return { ok: false, json: async () => null };
      return { ok: false, json: async () => null };
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByText(/conectado via z-api/i)).toBeInTheDocument());
  });

  it("mostra o número mascarado e o status quando conectado", async () => {
    mockedBackendFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        provider: "meta",
        display_phone_number: "+55 **** 4321",
        status: "connected",
        connected_at: "2026-07-08T12:00:00Z",
      }),
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByText("+55 **** 4321")).toBeInTheDocument());
    expect(screen.getByText("conectado")).toBeInTheDocument();
  });

  it("mostra estado desconectado com botão de reconectar", async () => {
    mockedBackendFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        provider: "meta",
        display_phone_number: "+55 **** 4321",
        status: "disconnected",
        connected_at: "2026-07-08T12:00:00Z",
      }),
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByText(/desconectado/i)).toBeInTheDocument());
    expect(screen.getByText("Reconectar")).toBeInTheDocument();
  });

  it("mostra a mensagem de fallback (sem quebrar) quando o servidor retorna detail como array (422)", async () => {
    mockedBackendFetch.mockImplementation(async (path: string) => {
      if (path === "whatsapp/connection") {
        return { ok: true, json: async () => null };
      }
      return {
        ok: false,
        json: async () => ({
          detail: [
            { type: "string_pattern_mismatch", loc: ["body", "pin"], msg: "String should match pattern" },
          ],
        }),
      };
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /whatsapp business oficial/i })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /whatsapp business oficial/i }));

    expect(screen.getByText("Phone Number ID")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Phone Number ID/i), { target: { value: "123" } });
    fireEvent.change(screen.getByLabelText(/WhatsApp Business Account ID/i), {
      target: { value: "456" },
    });
    fireEvent.change(screen.getByLabelText(/Access Token/i), { target: { value: "token" } });
    fireEvent.change(screen.getByLabelText(/PIN/i), { target: { value: "12a45" } });

    fireEvent.click(screen.getByRole("button", { name: /Conectar/i }));

    await waitFor(() => expect(screen.getByText(/Falha ao conectar/i)).toBeInTheDocument());
    expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
  });

  it("limpa as credenciais digitadas quando o usuário clica em Cancelar", async () => {
    mockedBackendFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        provider: "meta",
        display_phone_number: "+55 **** 4321",
        status: "connected",
        connected_at: "2026-07-08T12:00:00Z",
      }),
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByText("Trocar número")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Trocar número"));
    fireEvent.click(screen.getByRole("button", { name: /whatsapp business oficial/i }));

    const tokenInput = screen.getByLabelText(/Access Token/i) as HTMLInputElement;
    fireEvent.change(tokenInput, { target: { value: "secret-token" } });
    expect(tokenInput.value).toBe("secret-token");

    fireEvent.click(screen.getByText("Cancelar"));

    fireEvent.click(screen.getByText("Trocar número"));
    fireEvent.click(screen.getByRole("button", { name: /whatsapp business oficial/i }));

    const reopenedTokenInput = screen.getByLabelText(/Access Token/i) as HTMLInputElement;
    expect(reopenedTokenInput.value).toBe("");
  });

  it("mostra as instruções de webhook com os valores do endpoint e copia a URL", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    mockedBackendFetch.mockImplementation(async (path: string) => {
      if (path === "whatsapp/webhook-config") {
        return {
          ok: true,
          json: async () => ({
            callback_url: "https://api.exemplo.com.br/api/v1/webhooks/whatsapp",
            verify_token: "meu-verify-token",
          }),
        };
      }
      return { ok: true, json: async () => null };
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() =>
      expect(screen.getByText("Conectar o WhatsApp Business")).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Callback URL")).toHaveValue(
      "https://api.exemplo.com.br/api/v1/webhooks/whatsapp",
    );
    expect(screen.getByLabelText("Verify token")).toHaveValue("meu-verify-token");

    fireEvent.click(screen.getByRole("button", { name: "Copiar Callback URL" }));
    expect(screen.getByRole("button", { name: "Copiar Verify token" })).toBeInTheDocument();

    await waitFor(() => expect(screen.getByText("Copiado!")).toBeInTheDocument());
    expect(writeText).toHaveBeenCalledWith(
      "https://api.exemplo.com.br/api/v1/webhooks/whatsapp",
    );
  });

  it("instância gerenciada pela Advoxs (admin) pula form e busca o QR code automaticamente", async () => {
    const qrcodeFetch = vi.fn();
    mockedBackendFetch.mockImplementation(async (path: string) => {
      if (path === "whatsapp/connection") {
        return {
          ok: true,
          json: async () => ({
            provider: "zapi",
            display_phone_number: "Aguardando pareamento",
            status: "disconnected",
            connected_at: "2026-08-12T12:00:00Z",
            managed_by_advoxs: true,
          }),
        };
      }
      if (path === "whatsapp/webhook-config") return { ok: false, json: async () => null };
      if (path === "whatsapp/zapi-qrcode") {
        qrcodeFetch();
        return { ok: true, json: async () => ({ qrcode_base64: "data:image/png;base64,AAAA" }) };
      }
      if (path === "whatsapp/zapi-status") return { ok: true, json: async () => null };
      return { ok: false, json: async () => null };
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByAltText(/qr code/i)).toBeInTheDocument());
    expect(qrcodeFetch).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/a advoxs já configurou seu número/i)).toBeInTheDocument();
    expect(screen.queryByText(/como você quer conectar/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/instance id/i)).not.toBeInTheDocument();
  });

  it("instância gerenciada pela Advoxs, já conectada, não mostra o botão de reconectar", async () => {
    mockedBackendFetch.mockImplementation(async (path: string) => {
      if (path === "whatsapp/connection") {
        return {
          ok: true,
          json: async () => ({
            provider: "zapi",
            display_phone_number: "5511999998888",
            status: "connected",
            connected_at: "2026-08-12T12:00:00Z",
            managed_by_advoxs: true,
          }),
        };
      }
      return { ok: false, json: async () => null };
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByText(/conectado via z-api/i)).toBeInTheDocument());
    expect(screen.queryByText("Trocar número")).not.toBeInTheDocument();
    expect(screen.getByText("Desconectar")).toBeInTheDocument();
  });

  it("não mostra a seção de webhook quando o endpoint falha", async () => {
    mockedBackendFetch.mockImplementation(async (path: string) => {
      if (path === "whatsapp/webhook-config") {
        return { ok: false, json: async () => null };
      }
      return { ok: true, json: async () => null };
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /whatsapp business oficial/i })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /whatsapp business oficial/i }));

    expect(screen.getByText("Phone Number ID")).toBeInTheDocument();
    expect(screen.queryByText("Conectar o WhatsApp Business")).not.toBeInTheDocument();
  });
});
