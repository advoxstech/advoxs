import { render, screen, waitFor } from "@testing-library/react";
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
});
