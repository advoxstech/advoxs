import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SpendingChart } from "@/components/SpendingChart";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("SpendingChart", () => {
  it("carrega e renderiza o gráfico com os dados do período", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ by_month: [{ month: "2026-07", total_brl: 350 }] }),
    });

    render(<SpendingChart />);

    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    const [path] = mockedFetch.mock.calls[0]!;
    expect(path).toContain("billing/spending?from=");
  });

  it("muda o período ao clicar num preset", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ by_month: [] }) });

    render(<SpendingChart />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());

    mockedFetch.mockClear();
    screen.getByRole("button", { name: /90 dias/i }).click();

    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
  });
});
