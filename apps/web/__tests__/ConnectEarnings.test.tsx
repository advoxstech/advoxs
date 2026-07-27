import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectEarnings } from "@/components/ConnectEarnings";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("ConnectEarnings", () => {
  it("mostra saldo disponível/pendente e os repasses recentes", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        available_brl: 123.45,
        pending_brl: 20,
        recent_payouts: [{ amount_brl: 100, status: "paid", arrival_date: "2026-07-20" }],
      }),
    });

    render(<ConnectEarnings />);

    await waitFor(() => expect(screen.getByText("R$ 123,45")).toBeInTheDocument());
    expect(screen.getByText("R$ 20,00")).toBeInTheDocument();
    expect(screen.getByText("R$ 100,00")).toBeInTheDocument();
    expect(screen.getByText("pago")).toBeInTheDocument();
  });

  it("não renderiza nada quando a conta ainda não está configurada (404)", async () => {
    mockedFetch.mockResolvedValue({ ok: false, status: 404, json: async () => null });

    const { container } = render(<ConnectEarnings />);

    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("mostra erro quando a busca falha", async () => {
    mockedFetch.mockResolvedValue({ ok: false, status: 502, json: async () => null });

    render(<ConnectEarnings />);

    await waitFor(() =>
      expect(screen.getByText("Falha ao carregar o saldo — tente novamente.")).toBeInTheDocument(),
    );
  });

  it("sem repasses ainda, mostra só o saldo", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ available_brl: 0, pending_brl: 0, recent_payouts: [] }),
    });

    render(<ConnectEarnings />);

    await waitFor(() => expect(screen.getAllByText("R$ 0,00").length).toBe(2));
  });
});
