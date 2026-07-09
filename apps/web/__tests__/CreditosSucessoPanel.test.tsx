import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CreditosSucessoPanel } from "@/components/CreditosSucessoPanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedBackendFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedBackendFetch.mockReset();
});

describe("CreditosSucessoPanel", () => {
  it("mostra a mensagem de pronto quando o status confirma", async () => {
    mockedBackendFetch.mockResolvedValue({ ok: true, json: async () => ({ ready: true }) });

    render(<CreditosSucessoPanel sessionId="cs_123" />);

    await waitFor(() => expect(screen.getByText("Pagamento confirmado")).toBeInTheDocument());
    expect(screen.getByText("Voltar para o início")).toBeInTheDocument();
    expect(mockedBackendFetch).toHaveBeenCalledWith("billing/status?session_id=cs_123");
  });

  it("continua mostrando 'confirmando' enquanto ready é false", async () => {
    mockedBackendFetch.mockResolvedValue({ ok: true, json: async () => ({ ready: false }) });

    render(<CreditosSucessoPanel sessionId="cs_123" />);

    await waitFor(() => expect(mockedBackendFetch).toHaveBeenCalled());
    expect(screen.getByText("Confirmando seu pagamento…")).toBeInTheDocument();
  });

  it("sem session_id, mostra o estado de pronto imediatamente (sem polling)", () => {
    render(<CreditosSucessoPanel sessionId={null} />);

    expect(screen.getByText("Pagamento confirmado")).toBeInTheDocument();
    expect(mockedBackendFetch).not.toHaveBeenCalled();
  });

  it("mostra o estado de pronto após esgotar as tentativas sem confirmar", async () => {
    mockedBackendFetch.mockResolvedValue({ ok: true, json: async () => ({ ready: false }) });

    render(<CreditosSucessoPanel sessionId="cs_123" pollMs={0} />);

    await waitFor(
      () => expect(screen.getByText("Pagamento confirmado")).toBeInTheDocument(),
      { timeout: 3000 },
    );
    expect(screen.getByText("Voltar para o início")).toBeInTheDocument();
  });
});
