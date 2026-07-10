import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProfilePanel } from "@/components/ProfilePanel";
import { backendFetch } from "@/lib/client-api";
import type { Profile } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const PROFILE: Profile = {
  tenant_name: "Escritório Teste",
  email_contato: "a@b.com",
  has_logo: false,
  user_name: "Fulano",
  user_email: "fulano@b.com",
};

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("ProfilePanel", () => {
  it("carrega e exibe os dados do perfil", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => PROFILE });

    render(<ProfilePanel />);

    await waitFor(() =>
      expect(screen.getByDisplayValue("Escritório Teste")).toBeInTheDocument(),
    );
    expect(screen.getByText("fulano@b.com")).toBeInTheDocument();
  });

  it("salva o nome do escritório", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "profile" && (!init || init.method === undefined)) {
        return { ok: true, json: async () => PROFILE };
      }
      if (path === "profile" && init?.method === "PATCH") {
        expect(JSON.parse(init.body as string)).toEqual({ tenant_name: "Novo Nome" });
        return { ok: true, json: async () => ({ ...PROFILE, tenant_name: "Novo Nome" }) };
      }
      throw new Error(`chamada inesperada: ${path}`);
    });

    render(<ProfilePanel />);
    await waitFor(() => expect(screen.getByDisplayValue("Escritório Teste")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Nome do escritório"), {
      target: { value: "Novo Nome" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Salvar nome" }));

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith("profile", expect.objectContaining({ method: "PATCH" })),
    );
  });

  it("mostra erro quando a senha atual está errada", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "profile" && !init) {
        return { ok: true, json: async () => PROFILE };
      }
      if (path === "profile/password") {
        return { ok: false, status: 400, json: async () => ({ detail: "Senha atual incorreta" }) };
      }
      throw new Error(`chamada inesperada: ${path}`);
    });

    render(<ProfilePanel />);
    await waitFor(() => expect(screen.getByDisplayValue("Escritório Teste")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Senha atual"), { target: { value: "errada" } });
    fireEvent.change(screen.getByLabelText("Nova senha"), { target: { value: "nova12345" } });
    fireEvent.change(screen.getByLabelText("Confirmar nova senha"), {
      target: { value: "nova12345" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Trocar senha" }));

    await waitFor(() => expect(screen.getByText("Senha atual incorreta")).toBeInTheDocument());
  });

  it("mostra erro quando a confirmação de senha não bate, sem chamar a API", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => PROFILE });

    render(<ProfilePanel />);
    await waitFor(() => expect(screen.getByDisplayValue("Escritório Teste")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Senha atual"), { target: { value: "atual123" } });
    fireEvent.change(screen.getByLabelText("Nova senha"), { target: { value: "nova12345" } });
    fireEvent.change(screen.getByLabelText("Confirmar nova senha"), {
      target: { value: "diferente" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Trocar senha" }));

    expect(screen.getByText("As senhas não coincidem.")).toBeInTheDocument();
    expect(mockedFetch).not.toHaveBeenCalledWith(
      "profile/password",
      expect.anything(),
    );
  });

  it("renderiza o botão Sair da conta", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => PROFILE });

    render(<ProfilePanel />);
    await waitFor(() => expect(screen.getByDisplayValue("Escritório Teste")).toBeInTheDocument());

    expect(screen.getByRole("button", { name: "Sair da conta" })).toBeInTheDocument();
  });
});
