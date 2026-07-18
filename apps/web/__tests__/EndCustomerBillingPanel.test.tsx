import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EndCustomerBillingPanel } from "@/components/EndCustomerBillingPanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

function mockLoad(settings: unknown, packages: unknown[] = [], customers: unknown[] = []) {
  mockedFetch.mockImplementation(async (path: string) => {
    if (path === "end-customer-billing/settings") {
      return { ok: true, json: async () => settings };
    }
    if (path === "end-customer-billing/packages") {
      return { ok: true, json: async () => packages };
    }
    if (path === "end-customer-billing/customers") {
      return { ok: true, json: async () => customers };
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

  it("mostra a URL completa do webhook devolvida pelo backend, pra colar no Dashboard da Stripe", async () => {
    mockLoad({
      tenant_id: "11111111-1111-1111-1111-111111111111",
      enabled: false,
      billing_mode: "credits",
      stripe_secret_key_configured: false,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
      webhook_url:
        "https://api.exemplo.com.br/api/v1/webhooks/stripe/tenant/11111111-1111-1111-1111-111111111111",
    });

    render(<EndCustomerBillingPanel />);

    await waitFor(() =>
      expect(
        screen.getByText(
          "https://api.exemplo.com.br/api/v1/webhooks/stripe/tenant/11111111-1111-1111-1111-111111111111",
        ),
      ).toBeInTheDocument(),
    );
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
    // Sem isso, o checkbox continua marcado mesmo com o PATCH tendo falhado —
    // o usuário vê a caixa "salva" e só descobre que não persistiu ao
    // recarregar a página depois.
    expect(screen.getByLabelText(/cobrar meus clientes/i)).not.toBeChecked();
  });

  it("lista os pacotes já cadastrados", async () => {
    mockLoad(
      {
        enabled: true,
        billing_mode: "credits",
        stripe_secret_key_configured: true,
        stripe_webhook_secret_configured: true,
        end_customer_tokens_per_credit: 500,
      },
      [{ id: "p-1", name: "Básico", price_brl: "49.90", credits_granted: 500, active: true }],
    );

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByText("Básico")).toBeInTheDocument());
  });

  it("cria um pacote novo", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "end-customer-billing/packages" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({ id: "p-2", name: "Growth", price_brl: "99.90", credits_granted: 1000, active: true }),
        };
      }
      if (path === "end-customer-billing/settings") {
        return {
          ok: true,
          json: async () => ({
            enabled: true,
            billing_mode: "credits",
            stripe_secret_key_configured: true,
            stripe_webhook_secret_configured: true,
            end_customer_tokens_per_credit: 500,
          }),
        };
      }
      if (path === "end-customer-billing/packages") {
        return { ok: true, json: async () => [] };
      }
      return { ok: false, json: async () => null };
    });

    render(<EndCustomerBillingPanel />);
    await waitFor(() => expect(screen.getByLabelText(/nome do pacote/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/nome do pacote/i), { target: { value: "Growth" } });
    fireEvent.change(screen.getByLabelText(/preço/i), { target: { value: "99.90" } });
    fireEvent.change(screen.getByLabelText(/créditos/i), { target: { value: "1000" } });
    fireEvent.click(screen.getByRole("button", { name: /adicionar pacote/i }));

    await waitFor(() => expect(screen.getByText("Growth")).toBeInTheDocument());
  });

  it("exclui um pacote após confirmação", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "end-customer-billing/packages/p-1" && init?.method === "DELETE") {
        return { ok: true, json: async () => null };
      }
      if (path === "end-customer-billing/settings") {
        return {
          ok: true,
          json: async () => ({
            enabled: true,
            billing_mode: "credits",
            stripe_secret_key_configured: true,
            stripe_webhook_secret_configured: true,
            end_customer_tokens_per_credit: 500,
          }),
        };
      }
      if (path === "end-customer-billing/packages") {
        return {
          ok: true,
          json: async () => [{ id: "p-1", name: "Básico", price_brl: "49.90", credits_granted: 500, active: true }],
        };
      }
      return { ok: false, json: async () => null };
    });

    render(<EndCustomerBillingPanel />);
    await waitFor(() => expect(screen.getByText("Básico")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /excluir/i }));

    await waitFor(() => expect(screen.queryByText("Básico")).not.toBeInTheDocument());
    confirmSpy.mockRestore();
  });

  it("mostra erro quando a criação de pacote falha", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "end-customer-billing/packages" && init?.method === "POST") {
        return { ok: false, json: async () => ({ detail: "Nome do pacote já existe" }) };
      }
      if (path === "end-customer-billing/settings") {
        return {
          ok: true,
          json: async () => ({
            enabled: true,
            billing_mode: "credits",
            stripe_secret_key_configured: true,
            stripe_webhook_secret_configured: true,
            end_customer_tokens_per_credit: 500,
          }),
        };
      }
      if (path === "end-customer-billing/packages") {
        return { ok: true, json: async () => [] };
      }
      return { ok: false, json: async () => null };
    });

    render(<EndCustomerBillingPanel />);
    await waitFor(() => expect(screen.getByLabelText(/nome do pacote/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/nome do pacote/i), { target: { value: "Growth" } });
    fireEvent.change(screen.getByLabelText(/preço/i), { target: { value: "99.90" } });
    fireEvent.change(screen.getByLabelText(/créditos/i), { target: { value: "1000" } });
    fireEvent.click(screen.getByRole("button", { name: /adicionar pacote/i }));

    await waitFor(() => expect(screen.getByText(/nome do pacote já existe/i)).toBeInTheDocument());
    expect(screen.queryByText("Growth")).not.toBeInTheDocument();
  });

  it("mostra erro quando a exclusão de pacote falha", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "end-customer-billing/packages/p-1" && init?.method === "DELETE") {
        return { ok: false, json: async () => ({ detail: "Pacote em uso — não é possível excluir" }) };
      }
      if (path === "end-customer-billing/settings") {
        return {
          ok: true,
          json: async () => ({
            enabled: true,
            billing_mode: "credits",
            stripe_secret_key_configured: true,
            stripe_webhook_secret_configured: true,
            end_customer_tokens_per_credit: 500,
          }),
        };
      }
      if (path === "end-customer-billing/packages") {
        return {
          ok: true,
          json: async () => [{ id: "p-1", name: "Básico", price_brl: "49.90", credits_granted: 500, active: true }],
        };
      }
      return { ok: false, json: async () => null };
    });

    render(<EndCustomerBillingPanel />);
    await waitFor(() => expect(screen.getByText("Básico")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /excluir/i }));

    await waitFor(() =>
      expect(screen.getByText(/pacote em uso — não é possível excluir/i)).toBeInTheDocument(),
    );
    expect(screen.getByText("Básico")).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("mostra a lista de clientes finais quando a cobrança está habilitada", async () => {
    mockLoad(
      {
        enabled: true,
        billing_mode: "credits",
        stripe_secret_key_configured: true,
        stripe_webhook_secret_configured: true,
        end_customer_tokens_per_credit: 500,
      },
      [],
      [
        {
          contact_phone_number: "5511999990001",
          credit_balance: 120,
          total_purchased: 500,
          total_consumed: 380,
        },
      ],
    );

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByText("+55 11 99999-0001")).toBeInTheDocument());
    expect(screen.getByText("Clientes finais")).toBeInTheDocument();
  });

  it("não busca clientes finais quando a cobrança está desligada", async () => {
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      stripe_secret_key_configured: false,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByLabelText(/cobrar meus clientes/i)).not.toBeChecked());
    expect(screen.queryByText("Clientes finais")).not.toBeInTheDocument();
    expect(
      mockedFetch.mock.calls.some(([p]) => p === "end-customer-billing/customers"),
    ).toBe(false);
  });
});
