import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OnboardingProgress } from "@/components/OnboardingProgress";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({ backendFetch: vi.fn() }));
const backendFetchMock = vi.mocked(backendFetch);
const locationAssign = vi.fn();

const progress = {
  completed: false,
  main_configuration_complete: false,
  completed_steps: 2,
  total_steps: 6,
  steps: [
    {
      key: "agent",
      label: "Agente configurado",
      description: "Revise o agente.",
      kind: "main",
      completed: true,
      action_label: "Ver agentes",
      action_href: "/agentes",
    },
    {
      key: "whatsapp",
      label: "WhatsApp conectado",
      description: "Conecte um número.",
      kind: "main",
      completed: false,
      action_label: "Configurar WhatsApp",
      action_href: "/configuracoes/whatsapp",
    },
    {
      key: "knowledge_base",
      label: "Base de conhecimento preparada",
      description: "Adicione um arquivo.",
      kind: "recommended",
      completed: false,
      action_label: "Abrir base",
      action_href: "/base-de-conhecimento",
    },
    {
      key: "first_attendance",
      label: "Primeiro atendimento recebido",
      description: "Aguarde uma conversa real.",
      kind: "milestone",
      completed: false,
      action_label: "Ver conversas",
      action_href: "/conversas",
    },
  ],
};

beforeEach(() => {
  backendFetchMock.mockReset();
  locationAssign.mockReset();
  Object.defineProperty(window, "location", {
    value: { assign: locationAssign },
    writable: true,
    configurable: true,
  });
  backendFetchMock.mockResolvedValue({
    ok: true,
    json: async () => progress,
  } as Response);
});

describe("OnboardingProgress", () => {
  it("mostra o progresso real e links para as configurações", async () => {
    render(<OnboardingProgress />);

    expect(await screen.findByText("2 de 6 etapas")).toBeInTheDocument();
    expect(screen.getByText("Agente configurado")).toBeInTheDocument();
    expect(screen.getByText("WhatsApp conectado")).toBeInTheDocument();
    expect(screen.getByText("Base de conhecimento preparada")).toBeInTheDocument();
    expect(screen.getByText("Primeiro atendimento recebido")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Configurar WhatsApp" })).toHaveAttribute(
      "href",
      "/configuracoes/whatsapp",
    );
  });

  it("informa claramente que não bloqueia o painel nem o atendimento", async () => {
    render(<OnboardingProgress />);

    expect(
      await screen.findByText(/não bloqueia o painel nem o atendimento dos clientes/i),
    ).toBeInTheDocument();
  });

  it("atualiza a lista quando o usuário pede", async () => {
    render(<OnboardingProgress />);
    await screen.findByText("2 de 6 etapas");

    fireEvent.click(screen.getByRole("button", { name: "Atualizar progresso" }));

    await waitFor(() => expect(backendFetchMock).toHaveBeenCalledTimes(2));
  });

  it("permite continuar mesmo quando a API de conclusão falha", async () => {
    backendFetchMock.mockImplementation(async (path: string) => {
      if (path === "onboarding/complete") throw new Error("rede fora");
      return { ok: true, json: async () => progress } as Response;
    });
    render(<OnboardingProgress />);

    fireEvent.click(await screen.findByRole("button", { name: "Continuar para o painel" }));

    await waitFor(() => expect(locationAssign).toHaveBeenCalledWith("/inicio"));
  });

  it("oferece nova tentativa quando o progresso não pode ser carregado", async () => {
    backendFetchMock.mockRejectedValue(new Error("rede fora"));
    render(<OnboardingProgress />);

    expect(await screen.findByText("Não foi possível consultar o progresso agora.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continuar para o painel" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeInTheDocument();
  });
});
