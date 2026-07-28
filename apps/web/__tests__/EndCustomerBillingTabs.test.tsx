import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EndCustomerBillingTabs } from "@/components/EndCustomerBillingTabs";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

function mockRouting(enabled: boolean, billingProvider: string = "standalone") {
  mockedFetch.mockImplementation(async (path: string) => {
    if (path === "end-customer-billing/settings") {
      return {
        ok: true,
        json: async () => ({
          enabled,
          billing_mode: "credits",
          // Grandfathered (secret key já configurada) — este arquivo testa
          // a lógica de abas, não a ramificação Connect/standalone (isso é
          // coberto em EndCustomerBillingPanel.test.tsx); manter
          // stripe_secret_key_configured=true evita renderizar o
          // ConnectAccountOnboarding real (não mockado aqui).
          billing_provider: billingProvider,
          stripe_secret_key_configured: true,
          stripe_webhook_secret_configured: false,
          end_customer_tokens_per_credit: null,
          webhook_url: "",
        }),
      };
    }
    if (path.startsWith("end-customer-billing/revenue")) return { ok: true, json: async () => ({ by_month: [], by_customer: [] }) };
    if (path === "end-customer-billing/packages") return { ok: true, json: async () => [] };
    if (path === "end-customer-billing/customers") return { ok: true, json: async () => [] };
    if (path.startsWith("conversations/usage")) return { ok: true, json: async () => [] };
    return { ok: false, json: async () => null };
  });
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("EndCustomerBillingTabs", () => {
  it("começa na aba Configurações", async () => {
    mockRouting(false);

    render(<EndCustomerBillingTabs />);

    await waitFor(() => expect(screen.getByText(/secret key/i)).toBeInTheDocument());
  });

  it("esconde a aba Clientes quando a cobrança está desligada", async () => {
    mockRouting(false);

    render(<EndCustomerBillingTabs />);

    await waitFor(() => expect(screen.getByText(/secret key/i)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Clientes" })).not.toBeInTheDocument();
  });

  it("mostra a aba Clientes quando habilitado, e troca pra ela ao clicar", async () => {
    mockRouting(true);

    render(<EndCustomerBillingTabs />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Clientes" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Clientes" }));

    await waitFor(() =>
      expect(
        mockedFetch.mock.calls.some(([p]) => p === "end-customer-billing/customers"),
      ).toBe(true),
    );
  });

  it("aba Consumo mostra o relatório de conversas", async () => {
    mockRouting(false);

    render(<EndCustomerBillingTabs />);

    await waitFor(() => expect(screen.getByText(/secret key/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Consumo" }));

    await waitFor(() =>
      expect(screen.getByText("Nenhum consumo no período selecionado.")).toBeInTheDocument(),
    );
  });

  it("mostra a aba Faturamento quando habilitada com billing_provider connect", async () => {
    mockRouting(true, "connect");

    render(<EndCustomerBillingTabs />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /faturamento/i })).toBeInTheDocument(),
    );
  });

  it("não mostra a aba Faturamento quando habilitada mas billing_provider é standalone (tenant legado)", async () => {
    mockRouting(true, "standalone");

    render(<EndCustomerBillingTabs />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Clientes" })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /faturamento/i })).not.toBeInTheDocument();
  });
});
