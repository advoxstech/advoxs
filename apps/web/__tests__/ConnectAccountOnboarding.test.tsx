import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectAccountOnboarding } from "@/components/ConnectAccountOnboarding";
import { backendFetch } from "@/lib/client-api";
import { loadConnectAndInitialize } from "@stripe/connect-js";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

vi.mock("@stripe/connect-js", () => ({
  loadConnectAndInitialize: vi.fn().mockResolvedValue({
    create: vi.fn().mockReturnValue(document.createElement("div")),
  }),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;
const mockedLoadConnect = loadConnectAndInitialize as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
  mockedLoadConnect.mockClear();
});

describe("ConnectAccountOnboarding", () => {
  it("busca o client_secret e monta o componente de onboarding embutido", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ client_secret: "secret_abc" }),
    });

    render(<ConnectAccountOnboarding />);

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith(
        "end-customer-billing/connect-account",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("mostra erro quando a criação da sessão falha", async () => {
    mockedFetch.mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "Falha ao iniciar a configuração de pagamentos" }),
    });

    render(<ConnectAccountOnboarding />);

    await waitFor(() =>
      expect(screen.getByText(/falha ao iniciar a configuração/i)).toBeInTheDocument(),
    );
  });

  it("com visible=false, busca o client_secret mas não monta o widget da Stripe", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ client_secret: "secret_abc" }),
    });

    render(<ConnectAccountOnboarding visible={false} />);

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith(
        "end-customer-billing/connect-account",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(mockedLoadConnect).not.toHaveBeenCalled();
  });
});
