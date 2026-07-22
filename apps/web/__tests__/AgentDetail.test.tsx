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

function mockLoad(overrides?: { attached?: unknown[] }) {
  mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (!init && path === "agents") return { ok: true, json: async () => [AGENT] };
    if (!init && path === "agents/a1/knowledge-base-files") {
      return { ok: true, json: async () => overrides?.attached ?? [] };
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

  it("mostra a contagem de arquivos anexados com link pra base de conhecimento", async () => {
    mockLoad({ attached: [{ id: "f1", filename: "regimento.pdf", status: "ready" }] });

    render(<AgentDetail agentId="a1" />);

    await waitFor(() => expect(screen.getByText(/1 arquivo anexado/)).toBeInTheDocument());
    expect(
      screen.getByRole("link", { name: /gerenciar na base de conhecimento/ }),
    ).toHaveAttribute("href", "/base-de-conhecimento?agent_id=a1");
  });

  it("mostra plural quando há mais de um arquivo anexado", async () => {
    mockLoad({
      attached: [
        { id: "f1", filename: "regimento.pdf", status: "ready" },
        { id: "f2", filename: "modelo.docx", status: "ready" },
      ],
    });

    render(<AgentDetail agentId="a1" />);

    await waitFor(() => expect(screen.getByText(/2 arquivos anexados/)).toBeInTheDocument());
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
