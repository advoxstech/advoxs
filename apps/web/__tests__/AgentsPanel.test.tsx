import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { AgentsPanel } from "@/components/AgentsPanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const AGENTS = [
  {
    id: "a1",
    name: "Secretária",
    instructions: "x",
    is_entry_point: true,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
  },
  {
    id: "a2",
    name: "Condominial",
    instructions: "y",
    is_entry_point: false,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
  },
];

describe("AgentsPanel", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
  });

  it("lista os agentes com badge de ponto de entrada", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => AGENTS });

    render(<AgentsPanel />);

    await waitFor(() => expect(screen.getByText("Secretária")).toBeInTheDocument());
    expect(screen.getByText("Condominial")).toBeInTheDocument();
    expect(screen.getByText("ponto de entrada")).toBeInTheDocument();
  });

  it("cria um agente novo e recarrega a lista", async () => {
    const created = {
      id: "a3",
      name: "Novo",
      instructions: "z",
      is_entry_point: false,
      created_at: "2026-07-20T00:00:00Z",
      updated_at: "2026-07-20T00:00:00Z",
    };
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") return { ok: true, json: async () => created };
      return { ok: true, json: async () => [...AGENTS, created] };
    });

    render(<AgentsPanel />);
    await waitFor(() => expect(screen.getByText("Secretária")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Nome"), { target: { value: "Novo" } });
    fireEvent.change(screen.getByLabelText("Instruções"), { target: { value: "z" } });
    fireEvent.click(screen.getByRole("button", { name: "Criar agente" }));

    await waitFor(() => expect(screen.getByText("Novo")).toBeInTheDocument());
  });

  it("exclui um agente após confirmação", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "DELETE") return { ok: true, json: async () => null };
      return { ok: true, json: async () => AGENTS };
    });

    render(<AgentsPanel />);
    await waitFor(() => expect(screen.getByText("Secretária")).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole("button", { name: "Excluir" })[0]);

    await waitFor(() => expect(screen.queryByText("Secretária")).not.toBeInTheDocument());
    confirmSpy.mockRestore();
  });

  it("mostra erro do backend ao tentar apagar o ponto de entrada", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "DELETE") {
        return {
          ok: false,
          json: async () => ({
            detail:
              "Não é possível apagar o agente ponto de entrada — marque outro agente como ponto de entrada antes",
          }),
        };
      }
      return { ok: true, json: async () => AGENTS };
    });

    render(<AgentsPanel />);
    await waitFor(() => expect(screen.getByText("Secretária")).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole("button", { name: "Excluir" })[0]);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/ponto de entrada/));
  });
});
