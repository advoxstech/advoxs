import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OnboardingWizard } from "@/components/OnboardingWizard";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const backendFetchMock = vi.mocked(backendFetch);
const locationAssign = vi.fn();

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  backendFetchMock.mockReset();
  locationAssign.mockReset();
  Object.defineProperty(window, "location", {
    value: { assign: locationAssign },
    writable: true,
    configurable: true,
  });
  backendFetchMock.mockImplementation(async (path: string) => {
    if (String(path) === "whatsapp/webhook-config") {
      return jsonResponse({
        callback_url: "https://api.exemplo.com.br/api/v1/webhooks/whatsapp",
        verify_token: "meu-verify-token",
      });
    }
    return jsonResponse(null, 204);
  });
});

describe("OnboardingWizard", () => {
  it("navega do passo 1 ao 3 e conclui marcando completo", async () => {
    render(<OnboardingWizard />);

    expect(screen.getByText("Bem-vindo à Advoxs")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Começar" }));

    await waitFor(() =>
      expect(screen.getByText(/WhatsApp Business/)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Próximo" }));

    expect(screen.getByText(/Cobrança de clientes/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Concluir" }));

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(
          ([p, init]) => String(p) === "onboarding/complete" && init?.method === "POST",
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(locationAssign).toHaveBeenCalledWith("/inicio"));
  });

  it("mostra a callback URL e o verify token no passo do WhatsApp", async () => {
    render(<OnboardingWizard />);
    fireEvent.click(screen.getByRole("button", { name: "Começar" }));

    await waitFor(() =>
      expect(screen.getByLabelText("Callback URL")).toHaveValue(
        "https://api.exemplo.com.br/api/v1/webhooks/whatsapp",
      ),
    );
    expect(screen.getByLabelText("Verify token")).toHaveValue("meu-verify-token");
  });

  it("Configurar WhatsApp agora completa e navega pra config", async () => {
    render(<OnboardingWizard />);
    fireEvent.click(screen.getByRole("button", { name: "Começar" }));

    fireEvent.click(
      await screen.findByRole("button", { name: "Configurar WhatsApp agora" }),
    );

    await waitFor(() =>
      expect(locationAssign).toHaveBeenCalledWith("/configuracoes/whatsapp"),
    );
    expect(
      backendFetchMock.mock.calls.some(
        ([p, init]) => String(p) === "onboarding/complete" && init?.method === "POST",
      ),
    ).toBe(true);
  });

  it("Pular e testar os agentes está em todos os passos e navega pra aba Testes", async () => {
    render(<OnboardingWizard />);

    expect(screen.getByText("Pular e testar os agentes")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Pular e testar os agentes"));

    await waitFor(() =>
      expect(locationAssign).toHaveBeenCalledWith("/conversas?aba=testes"),
    );
  });

  it("POST falhando não impede a navegação", async () => {
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        throw new Error("rede fora");
      }
      return jsonResponse(null);
    });

    render(<OnboardingWizard />);
    fireEvent.click(screen.getByText("Pular e testar os agentes"));

    await waitFor(() =>
      expect(locationAssign).toHaveBeenCalledWith("/conversas?aba=testes"),
    );
  });

  it("Configurar cobrança completa e navega pra config de cobrança", async () => {
    render(<OnboardingWizard />);
    fireEvent.click(screen.getByRole("button", { name: "Começar" }));
    fireEvent.click(await screen.findByRole("button", { name: "Próximo" }));

    fireEvent.click(screen.getByRole("button", { name: "Configurar cobrança" }));

    await waitFor(() =>
      expect(locationAssign).toHaveBeenCalledWith("/configuracoes/cobranca-clientes"),
    );
    expect(
      backendFetchMock.mock.calls.some(
        ([p, init]) => String(p) === "onboarding/complete" && init?.method === "POST",
      ),
    ).toBe(true);
  });

  it("falha no webhook-config não quebra o passo 2 (campos somem, texto fica)", async () => {
    backendFetchMock.mockImplementation(async (path: string) => {
      if (String(path) === "whatsapp/webhook-config") {
        return jsonResponse(null, 500);
      }
      return jsonResponse(null, 204);
    });

    render(<OnboardingWizard />);
    fireEvent.click(screen.getByRole("button", { name: "Começar" }));

    await waitFor(() =>
      expect(screen.getByText("Conectar o WhatsApp Business")).toBeInTheDocument(),
    );
    expect(screen.queryByLabelText("Callback URL")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Próximo" })).toBeInTheDocument();
  });

  it("duplo-clique num botão de saída dispara um único POST", async () => {
    render(<OnboardingWizard />);

    const skip = screen.getByText("Pular e testar os agentes");
    fireEvent.click(skip);
    fireEvent.click(skip);

    await waitFor(() =>
      expect(locationAssign).toHaveBeenCalledWith("/conversas?aba=testes"),
    );
    const posts = backendFetchMock.mock.calls.filter(
      ([p, init]) => String(p) === "onboarding/complete" && init?.method === "POST",
    );
    expect(posts).toHaveLength(1);
  });
});
