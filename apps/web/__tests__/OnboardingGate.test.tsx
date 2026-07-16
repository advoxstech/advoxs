import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OnboardingGate } from "@/components/OnboardingGate";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

const backendFetchMock = vi.mocked(backendFetch);

beforeEach(() => {
  backendFetchMock.mockReset();
  replaceMock.mockReset();
});

describe("OnboardingGate", () => {
  it("redireciona pra /boas-vindas quando o onboarding não foi completado", async () => {
    backendFetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ completed: false }),
    } as Response);

    render(
      <OnboardingGate>
        <p>conteudo do dashboard</p>
      </OnboardingGate>,
    );

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/boas-vindas"));
    expect(screen.queryByText("conteudo do dashboard")).not.toBeInTheDocument();
  });

  it("renderiza os children quando completado", async () => {
    backendFetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ completed: true }),
    } as Response);

    render(
      <OnboardingGate>
        <p>conteudo do dashboard</p>
      </OnboardingGate>,
    );

    await waitFor(() =>
      expect(screen.getByText("conteudo do dashboard")).toBeInTheDocument(),
    );
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("fail-open: erro de rede renderiza os children", async () => {
    backendFetchMock.mockRejectedValue(new Error("rede fora"));

    render(
      <OnboardingGate>
        <p>conteudo do dashboard</p>
      </OnboardingGate>,
    );

    await waitFor(() =>
      expect(screen.getByText("conteudo do dashboard")).toBeInTheDocument(),
    );
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("fail-open: resposta 500 (não-ok) renderiza os children sem redirecionar", async () => {
    backendFetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: "erro interno" }),
    } as Response);

    render(
      <OnboardingGate>
        <p>conteudo do dashboard</p>
      </OnboardingGate>,
    );

    await waitFor(() =>
      expect(screen.getByText("conteudo do dashboard")).toBeInTheDocument(),
    );
    expect(replaceMock).not.toHaveBeenCalled();
  });
});
