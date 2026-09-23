import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OnboardingGate } from "@/components/OnboardingGate";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({ backendFetch: vi.fn() }));
const backendFetchMock = vi.mocked(backendFetch);

const progress = {
  completed: false,
  main_configuration_complete: false,
  completed_steps: 2,
  total_steps: 6,
  steps: [],
};

beforeEach(() => backendFetchMock.mockReset());

describe("OnboardingGate", () => {
  it("mostra o painel imediatamente e oferece acompanhamento opcional", async () => {
    backendFetchMock.mockResolvedValue({
      ok: true,
      json: async () => progress,
    } as Response);

    render(<OnboardingGate><p>conteudo do dashboard</p></OnboardingGate>);

    expect(screen.getByText("conteudo do dashboard")).toBeInTheDocument();
    expect(await screen.findByText("Continue configurando no seu ritmo")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver configuração inicial" })).toHaveAttribute(
      "href",
      "/configuracao-inicial",
    );
  });

  it("não exibe lembrete quando ele já foi dispensado", async () => {
    backendFetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ ...progress, completed: true }),
    } as Response);

    render(<OnboardingGate><p>conteudo do dashboard</p></OnboardingGate>);

    expect(screen.getByText("conteudo do dashboard")).toBeInTheDocument();
    await waitFor(() => expect(backendFetchMock).toHaveBeenCalledWith("onboarding"));
    expect(screen.queryByText("Continue configurando no seu ritmo")).not.toBeInTheDocument();
  });

  it("oculta o lembrete sem esconder o painel", async () => {
    backendFetchMock.mockResolvedValue({
      ok: true,
      json: async () => progress,
    } as Response);

    render(<OnboardingGate><p>conteudo do dashboard</p></OnboardingGate>);
    fireEvent.click(await screen.findByRole("button", { name: "Ocultar" }));

    expect(screen.getByText("conteudo do dashboard")).toBeInTheDocument();
    expect(screen.queryByText("Continue configurando no seu ritmo")).not.toBeInTheDocument();
    expect(backendFetchMock).toHaveBeenCalledWith("onboarding/complete", { method: "POST" });
  });

  it("falha aberta quando a API fica indisponível", async () => {
    backendFetchMock.mockResolvedValue({ ok: false, status: 503 } as Response);

    render(<OnboardingGate><p>conteudo do dashboard</p></OnboardingGate>);

    expect(screen.getByText("conteudo do dashboard")).toBeInTheDocument();
    await waitFor(() => expect(backendFetchMock).toHaveBeenCalled());
  });
});
