import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CreditosExtrato } from "@/components/CreditosExtrato";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("CreditosExtrato", () => {
  it("mostra estado vazio quando não há transações", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => [] });

    render(<CreditosExtrato />);

    await waitFor(() => expect(screen.getByText("Nenhuma transação ainda.")).toBeInTheDocument());
  });

  it("lista as transações com tipo traduzido e créditos formatados", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          id: "t1",
          type: "purchase",
          amount_credits: 1000,
          description: "Compra do pacote Starter",
          created_at: "2026-07-10T12:00:00Z",
        },
        {
          id: "t2",
          type: "consumption",
          amount_credits: -1.75,
          description: null,
          created_at: "2026-07-09T12:00:00Z",
        },
      ],
    });

    render(<CreditosExtrato />);

    await waitFor(() => expect(screen.getByText("Compra do pacote Starter")).toBeInTheDocument());
    expect(screen.getByText("+1.000")).toBeInTheDocument();
    expect(screen.getByText("Consumo")).toBeInTheDocument();
    expect(screen.getByText("-1,75")).toBeInTheDocument();
  });

  it("nunca menciona tokens na tela", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          id: "t1",
          type: "consumption",
          amount_credits: -2,
          description: "Consumo do agente",
          created_at: "2026-07-09T12:00:00Z",
        },
      ],
    });

    const { container } = render(<CreditosExtrato />);

    await waitFor(() => expect(screen.getByText("Consumo do agente")).toBeInTheDocument());
    expect(container.textContent?.toLowerCase()).not.toContain("token");
  });
});
