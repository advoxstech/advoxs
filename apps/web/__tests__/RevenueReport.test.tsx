import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RevenueReport } from "@/components/RevenueReport";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("RevenueReport", () => {
  it("carrega e mostra o total por cliente", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        by_month: [{ month: "2026-07", total_brl: 179.7 }],
        by_customer: [
          { contact_phone_number: "5511999998888", total_brl: 79.8 },
          { contact_phone_number: "5511999997777", total_brl: 99.9 },
        ],
      }),
    });

    render(<RevenueReport />);

    await waitFor(() => expect(screen.getByText(/79,80/)).toBeInTheDocument());
    expect(screen.getByText(/99,90/)).toBeInTheDocument();
  });

  it("muda o período ao clicar num preset", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ by_month: [], by_customer: [] }),
    });

    render(<RevenueReport />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());

    mockedFetch.mockClear();
    screen.getByRole("button", { name: /90 dias/i }).click();

    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    const [path] = mockedFetch.mock.calls[0]!;
    expect(path).toContain("end-customer-billing/revenue?from=");
  });

  it("mostra mensagem de vazio sem dados no período", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ by_month: [], by_customer: [] }),
    });

    render(<RevenueReport />);

    await waitFor(() =>
      expect(screen.getByText(/nenhum cliente comprou no período/i)).toBeInTheDocument(),
    );
  });
});
