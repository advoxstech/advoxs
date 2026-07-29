import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EndCustomerBillingPanel } from "@/components/EndCustomerBillingPanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

vi.mock("@/components/ConnectAccountOnboarding", () => ({
  ConnectAccountOnboarding: () => <div>onboarding-connect-mock</div>,
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
  it("mostra o toggle desligado e o webhook secret sem configurar (tenant standalone grandfathered)", async () => {
    // stripe_secret_key_configured: true simula um tenant grandfathered (já
    // configurou a secret key antes da migração pro Connect) — é a única
    // forma de ver o formulário antigo depois do fix; ver o teste "tenant
    // novo (...) vai direto pro onboarding Connect" pro caso oposto.
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      billing_provider: "standalone",
      stripe_account_id: null,
      stripe_account_status: null,
      stripe_secret_key_configured: true,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByLabelText(/cobrar meus clientes/i)).not.toBeChecked());
    expect(screen.getByText(/secret key/i)).toBeInTheDocument();
  });

  it("mostra a URL completa do webhook devolvida pelo backend, pra colar no Dashboard da Stripe", async () => {
    // Grandfathered (secret key já configurada) — é a única forma de o
    // formulário antigo (onde a URL do webhook aparece) renderizar.
    mockLoad({
      tenant_id: "11111111-1111-1111-1111-111111111111",
      enabled: false,
      billing_mode: "credits",
      billing_provider: "standalone",
      stripe_account_id: null,
      stripe_account_status: null,
      stripe_secret_key_configured: true,
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
    // Grandfathered — cenário de rotacionar uma secret key já configurada.
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      billing_provider: "standalone",
      stripe_account_id: null,
      stripe_account_status: null,
      stripe_secret_key_configured: true,
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

  it("mostra erro quando o PATCH falha (grandfathered, ex: erro de validação do backend)", async () => {
    // Grandfathered — o formulário antigo só é alcançável com a secret key
    // já configurada; o texto de erro devolvido pelo backend é só ilustrativo.
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
            billing_provider: "standalone",
            stripe_account_id: null,
            stripe_account_status: null,
            stripe_secret_key_configured: true,
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
        billing_provider: "standalone",
        stripe_account_id: null,
        stripe_account_status: null,
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
            billing_provider: "standalone",
            stripe_account_id: null,
            stripe_account_status: null,
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
            billing_provider: "standalone",
            stripe_account_id: null,
            stripe_account_status: null,
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
            billing_provider: "standalone",
            stripe_account_id: null,
            stripe_account_status: null,
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
            billing_provider: "standalone",
            stripe_account_id: null,
            stripe_account_status: null,
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

  it("mostra o onboarding Connect (não o formulário de secret key) quando billing_provider é connect", async () => {
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      billing_provider: "connect",
      stripe_account_id: "acct_123",
      stripe_account_status: "onboarding",
      stripe_secret_key_configured: false,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByText("onboarding-connect-mock")).toBeInTheDocument());
    expect(screen.queryByLabelText(/secret key/i)).not.toBeInTheDocument();
  });

  it("esconde o onboarding Connect quando a conta já está ativa", async () => {
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      billing_provider: "connect",
      stripe_account_id: "acct_123",
      stripe_account_status: "active",
      stripe_secret_key_configured: false,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);

    await waitFor(() =>
      expect(screen.getByLabelText(/cobrar meus clientes/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText("onboarding-connect-mock")).not.toBeInTheDocument();
  });

  it("mostra a mensagem de conta configurada quando a conta já está ativa", async () => {
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      billing_provider: "connect",
      stripe_account_id: "acct_123",
      stripe_account_status: "active",
      stripe_secret_key_configured: false,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);

    await waitFor(() =>
      expect(screen.getByText(/sua conta stripe já está configurada/i)).toBeInTheDocument(),
    );
  });

  it("não mostra a mensagem de conta configurada durante o onboarding", async () => {
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      billing_provider: "connect",
      stripe_account_id: "acct_123",
      stripe_account_status: "onboarding",
      stripe_secret_key_configured: false,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByText("onboarding-connect-mock")).toBeInTheDocument());
    expect(screen.queryByText(/sua conta stripe já está configurada/i)).not.toBeInTheDocument();
  });

  it("persiste o toggle de cobrança no onboarding Connect via PATCH", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "end-customer-billing/settings" && init?.method === "PATCH") {
        return {
          ok: true,
          json: async () => ({
            enabled: true,
            billing_mode: "credits",
            billing_provider: "connect",
            stripe_account_id: "acct_123",
            stripe_account_status: "active",
            stripe_secret_key_configured: false,
            stripe_webhook_secret_configured: false,
            end_customer_tokens_per_credit: null,
          }),
        };
      }
      if (path === "end-customer-billing/settings") {
        return {
          ok: true,
          json: async () => ({
            enabled: false,
            billing_mode: "credits",
            billing_provider: "connect",
            stripe_account_id: "acct_123",
            stripe_account_status: "active",
            stripe_secret_key_configured: false,
            stripe_webhook_secret_configured: false,
            end_customer_tokens_per_credit: null,
          }),
        };
      }
      if (path === "end-customer-billing/packages") {
        return { ok: true, json: async () => [] };
      }
      return { ok: false, json: async () => null };
    });

    render(<EndCustomerBillingPanel />);
    await waitFor(() => expect(screen.getByLabelText(/cobrar meus clientes/i)).not.toBeChecked());

    fireEvent.click(screen.getByLabelText(/cobrar meus clientes/i));
    fireEvent.click(screen.getByRole("button", { name: /salvar alterações/i }));

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
    expect(body).toEqual({ enabled: true });
    expect(body.stripe_secret_key).toBeUndefined();
    expect(body.stripe_webhook_secret).toBeUndefined();

    await waitFor(() => expect(screen.getByLabelText(/cobrar meus clientes/i)).toBeChecked());
  });

  it("tenant novo (standalone default, secret key nunca configurada) vai direto pro onboarding Connect, não o formulário antigo", async () => {
    // Reproduz o gap: `GET /settings` sem row ainda devolve billing_provider
    // "standalone" (valor neutro da coluna, não um sinal positivo de que o
    // tenant configurou o modelo antigo) + stripe_secret_key_configured
    // false. Sem checar essa segunda flag, esse tenant cairia no formulário
    // antigo — cujo submit é garantidamente rejeitado (400) pelo backend
    // pra tenant sem row (ver update_settings), formando um beco sem saída:
    // sem botão/link pra iniciar o onboarding Connect em lugar nenhum.
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      billing_provider: "standalone",
      stripe_account_id: null,
      stripe_account_status: null,
      stripe_secret_key_configured: false,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByText("onboarding-connect-mock")).toBeInTheDocument());
    expect(screen.queryByLabelText(/secret key/i)).not.toBeInTheDocument();
  });

  it("mostra o formulário antigo de secret key pra tenant standalone grandfathered (secret key já configurada)", async () => {
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      billing_provider: "standalone",
      stripe_account_id: null,
      stripe_account_status: null,
      stripe_secret_key_configured: true,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByLabelText(/secret key/i)).toBeInTheDocument());
    expect(screen.queryByText("onboarding-connect-mock")).not.toBeInTheDocument();
  });

  it("mostra o seletor de tipo de pacote quando billing_provider é connect", async () => {
    mockLoad({
      enabled: false,
      billing_mode: "credits",
      billing_provider: "connect",
      stripe_account_id: "acct_123",
      stripe_account_status: "active",
      stripe_secret_key_configured: false,
      stripe_webhook_secret_configured: false,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByLabelText(/tipo de pacote/i)).toBeInTheDocument());
  });

  it("não mostra o seletor de tipo de pacote quando billing_provider é standalone (grandfathered)", async () => {
    mockLoad({
      enabled: true,
      billing_mode: "credits",
      billing_provider: "standalone",
      stripe_account_id: null,
      stripe_account_status: null,
      stripe_secret_key_configured: true,
      stripe_webhook_secret_configured: true,
      end_customer_tokens_per_credit: null,
    });

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByLabelText(/nome do pacote/i)).toBeInTheDocument());
    expect(screen.queryByLabelText(/tipo de pacote/i)).not.toBeInTheDocument();
  });

  it("esconde o campo créditos e envia kind=subscription quando assinatura mensal é escolhida", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "end-customer-billing/packages" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({ id: "p-3", name: "Ilimitado", price_brl: "99.90", kind: "subscription", credits_granted: null, active: true }),
        };
      }
      if (path === "end-customer-billing/settings") {
        return {
          ok: true,
          json: async () => ({
            enabled: false, billing_mode: "credits", billing_provider: "connect",
            stripe_account_id: "acct_123", stripe_account_status: "active",
            stripe_secret_key_configured: false, stripe_webhook_secret_configured: false,
            end_customer_tokens_per_credit: null,
          }),
        };
      }
      if (path === "end-customer-billing/packages") return { ok: true, json: async () => [] };
      return { ok: false, json: async () => null };
    });

    render(<EndCustomerBillingPanel />);
    await waitFor(() => expect(screen.getByLabelText(/tipo de pacote/i)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/tipo de pacote/i), { target: { value: "subscription" } });
    expect(screen.queryByLabelText(/créditos/i)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/nome do pacote/i), { target: { value: "Ilimitado" } });
    fireEvent.change(screen.getByLabelText(/preço/i), { target: { value: "99.90" } });
    fireEvent.click(screen.getByRole("button", { name: /adicionar pacote/i }));

    await waitFor(() => expect(screen.getByText("Ilimitado")).toBeInTheDocument());
    const postCall = mockedFetch.mock.calls.find(
      ([path, init]) => path === "end-customer-billing/packages" && init?.method === "POST",
    );
    const body = JSON.parse(postCall![1].body as string);
    expect(body.kind).toBe("subscription");
    expect(body.credits_granted).toBeUndefined();
  });

  it("mostra badge Mensal/Avulso na listagem de pacotes", async () => {
    mockLoad(
      {
        enabled: true, billing_mode: "credits", billing_provider: "connect",
        stripe_account_id: "acct_123", stripe_account_status: "active",
        stripe_secret_key_configured: false, stripe_webhook_secret_configured: false,
        end_customer_tokens_per_credit: null,
      },
      [
        { id: "p-1", name: "Básico", price_brl: "49.90", kind: "one_time", credits_granted: 500, active: true },
        { id: "p-2", name: "Ilimitado", price_brl: "99.90", kind: "subscription", credits_granted: null, active: true },
      ],
    );

    render(<EndCustomerBillingPanel />);

    await waitFor(() => expect(screen.getByText("Básico")).toBeInTheDocument());
    expect(screen.getByText("Avulso")).toBeInTheDocument();
    expect(screen.getByText("Mensal")).toBeInTheDocument();
  });
});
