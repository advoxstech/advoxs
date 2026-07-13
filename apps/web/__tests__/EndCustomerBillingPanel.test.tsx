import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EndCustomerBillingPanel } from "@/components/EndCustomerBillingPanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

function mockLoad(settings: unknown, packages: unknown[] = []) {
  mockedFetch.mockImplementation(async (path: string) => {
    if (path === "end-customer-billing/settings") {
      return { ok: true, json: async () => settings };
    }
    if (path === "end-customer-billing/packages") {
      return { ok: true, json: async () => packages };
    }
    return { ok: false, json: async () => null };
  });
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("EndCustomerBillingPanel", () => {
  it("mostra o toggle desligado e sem secrets configuradas por padrão", async () => {
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      stripe_secret_key_configured: false,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByLabelText(/cobrar meus clientes/i)).not.toBeChecked());
    expect(screen.getByText(/secret key/i)).toBeInTheDocument();
  });

  it("envia PATCH com a secret key digitada", async () => {
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      stripe_secret_key_configured: false,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);
    await waitFor(() => expect(screen.getByLabelText(/secret key/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/secret key/i), { target: { value: "sk_test_123" } });
    fireEvent.click(screen.getByRole("button", { name: /salvar configuração/i }));

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith(
        "end-customer-billing/settings",
        expect.objectContaining({ method: "PATCH" }),
      ),
    );
    const patchCall = mockedFetch.mock.calls.find(
      ([path, init]) => path === "end-customer-billing/settings" && init?.method === "PATCH",
    );
    const body = JSON.parse(patchCall![1].body as string);
    expect(body.stripe_secret_key).toBe("sk_test_123");
  });

  it("mostra erro quando o PATCH falha (ex: habilitar sem secret key)", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "end-customer-billing/settings" && init?.method === "PATCH") {
        return { ok: false, json: async () => ({ detail: "Configure a secret key da Stripe antes de ativar" }) };
      }
      if (path === "end-customer-billing/settings") {
        return {
          ok: true,
          json: async () => ({
            enabled: false,
            billing_mode: "credits",
            stripe_secret_key_configured: false,
            stripe_webhook_secret_configured: false,
            end_customer_tokens_per_credit: null,
          }),
        };
      }
      return { ok: true, json: async () => [] };
    });

    render(<EndCustomerBillingPanel />);
    await waitFor(() => expect(screen.getByLabelText(/cobrar meus clientes/i)).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText(/cobrar meus clientes/i));
    fireEvent.click(screen.getByRole("button", { name: /salvar configuração/i }));

    await waitFor(() =>
      expect(screen.getByText(/configure a secret key/i)).toBeInTheDocument(),
    );
  });
});
