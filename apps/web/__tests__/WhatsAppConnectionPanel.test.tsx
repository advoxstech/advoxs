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
  it("mostra o formulário quando não há conexão", async () => {
    mockedBackendFetch.mockResolvedValue({ ok: true, json: async () => null });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByText("Phone Number ID")).toBeInTheDocument());
  });

  it("mostra o número mascarado e o status quando conectado", async () => {
    mockedBackendFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        display_phone_number: "+55 **** 4321",
        status: "connected",
        connected_at: "2026-07-08T12:00:00Z",
      }),
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByText("+55 **** 4321")).toBeInTheDocument());
    expect(screen.getByText(/conectado/i)).toBeInTheDocument();
  });

  it("mostra estado desconectado com botão de reconectar", async () => {
    mockedBackendFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
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

    await waitFor(() => expect(screen.getByText("Phone Number ID")).toBeInTheDocument());

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
        display_phone_number: "+55 **** 4321",
        status: "connected",
        connected_at: "2026-07-08T12:00:00Z",
      }),
    });

    render(<WhatsAppConnectionPanel />);

    await waitFor(() => expect(screen.getByText("Trocar número")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Trocar número"));

    const tokenInput = screen.getByLabelText(/Access Token/i) as HTMLInputElement;
    fireEvent.change(tokenInput, { target: { value: "secret-token" } });
    expect(tokenInput.value).toBe("secret-token");

    fireEvent.click(screen.getByText("Cancelar"));

    fireEvent.click(screen.getByText("Trocar número"));

    const reopenedTokenInput = screen.getByLabelText(/Access Token/i) as HTMLInputElement;
    expect(reopenedTokenInput.value).toBe("");
  });
});
