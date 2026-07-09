import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LowBalanceBanner } from "@/components/LowBalanceBanner";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("LowBalanceBanner", () => {
  it("mostra o aviso quando o saldo está esgotado (0)", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ credit_balance: 0 }) });

    render(<LowBalanceBanner />);

    await waitFor(() =>
      expect(screen.getByText(/saldo de créditos está esgotado/i)).toBeInTheDocument(),
    );
    expect(screen.getByRole("link", { name: /comprar créditos/i })).toHaveAttribute(
      "href",
      "/creditos",
    );
  });

  it("mostra o aviso quando o saldo está negativo", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ credit_balance: -50 }) });

    render(<LowBalanceBanner />);

    await waitFor(() =>
      expect(screen.getByText(/saldo de créditos está esgotado/i)).toBeInTheDocument(),
    );
  });

  it("não mostra nada quando o saldo é positivo", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ credit_balance: 500 }) });

    render(<LowBalanceBanner />);

    await waitFor(() => expect(mockedFetch).toHaveBeenCalledWith("billing/balance"));
    expect(screen.queryByText(/saldo de créditos está esgotado/i)).not.toBeInTheDocument();
  });

  it("não quebra quando a busca de saldo falha (fail-safe silencioso)", async () => {
    mockedFetch.mockRejectedValue(new Error("network error"));

    render(<LowBalanceBanner />);

    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    expect(screen.queryByText(/saldo de créditos está esgotado/i)).not.toBeInTheDocument();
  });
});
