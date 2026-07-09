import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CreditosPanel } from "@/components/CreditosPanel";
import { backendFetch } from "@/lib/client-api";
import type { CreditPackage } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const PACKAGES: CreditPackage[] = [
  { id: "p1", name: "Starter", price_brl: 100, credits_granted: 1000 },
  { id: "p2", name: "Growth", price_brl: 250, credits_granted: 2750 },
];

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("CreditosPanel", () => {
  it("carrega e exibe o saldo atual", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ credit_balance: 1500 }) });

    render(<CreditosPanel packages={PACKAGES} sessionId={null} />);

    await waitFor(() => expect(screen.getByText("1500 créditos")).toBeInTheDocument());
  });

  it("renderiza os pacotes recebidos por prop", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ credit_balance: 0 }) });

    render(<CreditosPanel packages={PACKAGES} sessionId={null} />);

    expect(screen.getByText("Starter")).toBeInTheDocument();
    expect(screen.getByText("Growth")).toBeInTheDocument();
  });

  it("clicar em Comprar chama o checkout com o pacote certo", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "billing/balance") {
        return { ok: true, json: async () => ({ credit_balance: 0 }) };
      }
      if (path === "billing/checkout") {
        expect(JSON.parse(init?.body as string)).toEqual({ credit_package_id: "p2" });
        return { ok: true, json: async () => ({ checkout_url: "https://checkout.stripe.com/x" }) };
      }
      throw new Error(`chamada inesperada: ${path}`);
    });

    render(<CreditosPanel packages={PACKAGES} sessionId={null} />);
    await waitFor(() => expect(screen.getByText("Growth")).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole("button", { name: "Comprar" })[1]);

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith(
        "billing/checkout",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("mantém o botão em 'Redirecionando…' após o sucesso e não dispara um segundo checkout", async () => {
    let checkoutCalls = 0;
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "billing/balance") {
        return { ok: true, json: async () => ({ credit_balance: 0 }) };
      }
      if (path === "billing/checkout") {
        checkoutCalls += 1;
        void init;
        return { ok: true, json: async () => ({ checkout_url: "https://checkout.stripe.com/x" }) };
      }
      throw new Error(`chamada inesperada: ${path}`);
    });

    render(<CreditosPanel packages={PACKAGES} sessionId={null} />);
    await waitFor(() => expect(screen.getByText("Growth")).toBeInTheDocument());

    const buttons = screen.getAllByRole("button", { name: "Comprar" });
    fireEvent.click(buttons[1]);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Redirecionando…" })).toBeInTheDocument(),
    );

    const redirectingButton = screen.getByRole("button", { name: "Redirecionando…" });
    expect(redirectingButton).toBeDisabled();

    // Um segundo clique (duplo-clique real, ou clique em outro botão) não deve
    // disparar uma segunda chamada de checkout: o botão permanece desabilitado
    // mesmo depois do primeiro await resolver, até a navegação de fato ocorrer.
    fireEvent.click(redirectingButton);
    const otherButton = screen.getAllByRole("button", { name: "Comprar" })[0];
    fireEvent.click(otherButton);

    expect(screen.getByRole("button", { name: "Redirecionando…" })).toBeDisabled();
    expect(checkoutCalls).toBe(1);
  });

  it("mostra 'Confirmando' enquanto o pagamento não é confirmado, com sessionId", async () => {
    mockedFetch.mockImplementation(async (path: string) => {
      if (path === "billing/balance") {
        return { ok: true, json: async () => ({ credit_balance: 0 }) };
      }
      if (path.startsWith("billing/status")) {
        return { ok: true, json: async () => ({ ready: false }) };
      }
      throw new Error(`chamada inesperada: ${path}`);
    });

    render(<CreditosPanel packages={PACKAGES} sessionId="cs_123" pollMs={0} />);

    await waitFor(() => expect(screen.getByText("Confirmando seu pagamento…")).toBeInTheDocument());
  });

  it("some com 'Confirmando' e atualiza o saldo quando o pagamento confirma", async () => {
    let statusReady = false;
    mockedFetch.mockImplementation(async (path: string) => {
      if (path === "billing/balance") {
        return {
          ok: true,
          json: async () => ({ credit_balance: statusReady ? 2750 : 0 }),
        };
      }
      if (path.startsWith("billing/status")) {
        statusReady = true;
        return { ok: true, json: async () => ({ ready: true }) };
      }
      throw new Error(`chamada inesperada: ${path}`);
    });

    render(<CreditosPanel packages={PACKAGES} sessionId="cs_123" pollMs={0} />);

    await waitFor(() =>
      expect(screen.queryByText("Confirmando seu pagamento…")).not.toBeInTheDocument(),
    );
    await waitFor(() => expect(screen.getByText("2750 créditos")).toBeInTheDocument());
  });
});
