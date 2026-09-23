import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FinancialUsagePanel } from "@/components/FinancialUsagePanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({ backendFetch: vi.fn() }));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const report = {
  summary: {
    executions: 2,
    operational_credits: 12.5,
    billed_credits: 5,
    shortfall_credits: 0,
    subscription_credits: 7.5,
    tenant_credits: 5,
    end_customer_credits: 0,
    document_credits: 0,
    tokens_input: 1000,
    tokens_output: 500,
  },
  items: [
    {
      id: "usage-1",
      contact_phone_number: "5511999998888",
      funding_source: "end_customer_subscription",
      operational_credits: 7.5,
      billed_credits: 0,
      shortfall_credits: 0,
      created_at: "2026-09-22T12:00:00Z",
    },
  ],
};

beforeEach(() => mockedFetch.mockReset());

describe("FinancialUsagePanel", () => {
  it("mostra custo real separado do valor cobrado", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => report });

    render(<FinancialUsagePanel />);

    await waitFor(() => expect(screen.getByText("12,5")).toBeInTheDocument());
    expect(screen.getByText("Assinatura do cliente")).toBeInTheDocument();
    expect(screen.getByText("•••• 8888")).toBeInTheDocument();
    expect(mockedFetch.mock.calls[0]![0]).toContain("billing/usage?from=");
  });

  it("informa falha sem exibir dados antigos", async () => {
    mockedFetch.mockResolvedValue({ ok: false, json: async () => null });

    render(<FinancialUsagePanel />);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Não foi possível carregar o consumo financeiro.",
      ),
    );
  });
});
