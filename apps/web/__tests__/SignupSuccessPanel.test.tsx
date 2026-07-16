import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SignupSuccessPanel } from "@/components/SignupSuccessPanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

vi.mock("@/app/cadastro/actions", () => ({
  autoLogin: vi.fn(),
}));

const mockedBackendFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedBackendFetch.mockReset();
});

describe("SignupSuccessPanel", () => {
  it("mostra a mensagem de pronto quando o status confirma", async () => {
    mockedBackendFetch.mockResolvedValue({ ok: true, json: async () => ({ ready: true }) });

    render(<SignupSuccessPanel sessionId="cs_123" />);

    await waitFor(() => expect(screen.getByText("Pagamento confirmado")).toBeInTheDocument());
    expect(screen.getByText("Ir para o login")).toBeInTheDocument();
    expect(mockedBackendFetch).toHaveBeenCalledWith(
      "signup/status?session_id=cs_123",
    );
  });

  it("continua mostrando 'confirmando' enquanto ready é false", async () => {
    mockedBackendFetch.mockResolvedValue({ ok: true, json: async () => ({ ready: false }) });

    render(<SignupSuccessPanel sessionId="cs_123" />);

    await waitFor(() => expect(mockedBackendFetch).toHaveBeenCalled());
    expect(screen.getByText("Confirmando seu pagamento…")).toBeInTheDocument();
  });

  it("sem session_id, mostra o estado de pronto imediatamente (sem polling)", () => {
    render(<SignupSuccessPanel sessionId={null} />);

    expect(screen.getByText("Pagamento confirmado")).toBeInTheDocument();
    expect(mockedBackendFetch).not.toHaveBeenCalled();
  });

  it("mostra o estado de pronto (tom neutro) após esgotar as tentativas sem confirmar", async () => {
    mockedBackendFetch.mockResolvedValue({ ok: true, json: async () => ({ ready: false }) });

    render(<SignupSuccessPanel sessionId="cs_123" pollMs={0} />);

    await waitFor(
      () => expect(screen.getByText("Pagamento confirmado")).toBeInTheDocument(),
      { timeout: 3000 },
    );
    expect(screen.getByText("Ir para o login")).toBeInTheDocument();
  });

  it("chama autoLogin quando o status traz login_token", async () => {
    const { autoLogin } = await import("@/app/cadastro/actions");
    vi.mocked(autoLogin).mockResolvedValue({ error: null });
    mockedBackendFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ ready: true, login_token: "tok-1" }),
    });

    render(<SignupSuccessPanel sessionId="cs_123" />);

    await waitFor(() => expect(autoLogin).toHaveBeenCalledWith("tok-1"));
    expect(screen.getByText(/Entrando/)).toBeInTheDocument();
  });

  it("sem login_token mantém o botão de ir para o login", async () => {
    mockedBackendFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ ready: true, login_token: null }),
    });

    render(<SignupSuccessPanel sessionId="cs_123" />);

    await waitFor(() => expect(screen.getByText("Ir para o login")).toBeInTheDocument());
  });

  it("erro na action cai no fallback com o botão de login", async () => {
    const { autoLogin } = await import("@/app/cadastro/actions");
    vi.mocked(autoLogin).mockResolvedValue({ error: "invalid" });
    mockedBackendFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ ready: true, login_token: "tok-1" }),
    });

    render(<SignupSuccessPanel sessionId="cs_123" />);

    await waitFor(() => expect(screen.getByText("Ir para o login")).toBeInTheDocument());
  });
});
