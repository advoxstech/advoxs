import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EndCustomerList } from "@/components/EndCustomerList";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("EndCustomerList", () => {
  it("mostra estado vazio quando não há clientes", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => [] });

    render(<EndCustomerList />);

    await waitFor(() =>
      expect(screen.getByText("Nenhum cliente comprou créditos ainda.")).toBeInTheDocument(),
    );
  });

  it("lista clientes com saldo, comprado e consumido formatados", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          contact_phone_number: "5511999990001",
          credit_balance: 120,
          total_purchased: 500,
          total_consumed: 380,
        },
      ],
    });

    render(<EndCustomerList />);

    await waitFor(() => expect(screen.getByText("+55 11 99999-0001")).toBeInTheDocument());
    expect(screen.getByText("120")).toBeInTheDocument();
    expect(screen.getByText("500")).toBeInTheDocument();
    expect(screen.getByText("380")).toBeInTheDocument();
  });

  it("nunca menciona tokens na tela", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          contact_phone_number: "5511999990001",
          credit_balance: 120,
          total_purchased: 500,
          total_consumed: 380,
        },
      ],
    });

    const { container } = render(<EndCustomerList />);

    await waitFor(() => expect(screen.getByText("120")).toBeInTheDocument());
    expect(container.textContent?.toLowerCase()).not.toContain("token");
  });
});
