import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { AgentDetail } from "@/components/AgentDetail";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const AGENT = {
  id: "a1",
  name: "Secretária",
  instructions: "Você é a secretária.",
  is_entry_point: true,
  created_at: "2026-07-20T00:00:00Z",
  updated_at: "2026-07-20T00:00:00Z",
};

function mockLoad(overrides?: { attached?: unknown[]; all?: unknown[] }) {
  mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (!init && path === "agents") return { ok: true, json: async () => [AGENT] };
    if (!init && path === "agents/a1/knowledge-base-files") {
      return { ok: true, json: async () => overrides?.attached ?? [] };
    }
    if (!init && path === "knowledge-base/files") {
      return { ok: true, json: async () => overrides?.all ?? [] };
    }
    return { ok: true, json: async () => null };
  });
}

describe("AgentDetail", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
  });

  it("carrega e preenche o formulário com os dados do agente", async () => {
    mockLoad();

    render(<AgentDetail agentId="a1" />);

    await waitFor(() => expect(screen.getByDisplayValue("Secretária")).toBeInTheDocument());
    expect(screen.getByDisplayValue("Você é a secretária.")).toBeInTheDocument();
  });

  it("mostra 'agente não encontrado' quando o id não existe na lista", async () => {
    mockedFetch.mockImplementation(async (path: string) => {
      if (path === "agents") return { ok: true, json: async () => [AGENT] };
      return { ok: true, json: async () => [] };
    });

    render(<AgentDetail agentId="inexistente" />);

    await waitFor(() => expect(screen.getByText("Agente não encontrado.")).toBeInTheDocument());
  });

  it("lista os arquivos anexados e omite eles do seletor de anexar", async () => {
    mockLoad({
      attached: [{ id: "f1", filename: "regimento.pdf", status: "ready" }],
      all: [
        { id: "f1", filename: "regimento.pdf", status: "ready" },
        { id: "f2", filename: "modelo.docx", status: "ready" },
      ],
    });

    render(<AgentDetail agentId="a1" />);

    await waitFor(() => expect(screen.getByText("regimento.pdf")).toBeInTheDocument());
    expect(screen.getByRole("option", { name: "modelo.docx" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "regimento.pdf" })).not.toBeInTheDocument();
  });

  it("desanexa um arquivo após confirmação", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "DELETE") return { ok: true, json: async () => null };
      if (path === "agents") return { ok: true, json: async () => [AGENT] };
      if (path === "agents/a1/knowledge-base-files") {
        return {
          ok: true,
          json: async () => [{ id: "f1", filename: "regimento.pdf", status: "ready" }],
        };
      }
      return { ok: true, json: async () => [] };
    });

    render(<AgentDetail agentId="a1" />);
    await waitFor(() => expect(screen.getByText("regimento.pdf")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Desanexar" }));

    await waitFor(() => expect(screen.queryByText("regimento.pdf")).not.toBeInTheDocument());
    confirmSpy.mockRestore();
  });

  it("salva as alterações do formulário", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        return { ok: true, json: async () => ({ ...AGENT, name: "Nova Secretária" }) };
      }
      if (!init && path === "agents") return { ok: true, json: async () => [AGENT] };
      return { ok: true, json: async () => [] };
    });

    render(<AgentDetail agentId="a1" />);
    await waitFor(() => expect(screen.getByDisplayValue("Secretária")).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue("Secretária"), {
      target: { value: "Nova Secretária" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Salvar" }));

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith(
        "agents/a1",
        expect.objectContaining({ method: "PATCH" }),
      ),
    );
  });
});
