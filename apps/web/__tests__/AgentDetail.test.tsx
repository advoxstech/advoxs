import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { AgentDetail } from "@/components/AgentDetail";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));
vi.mock("@/components/TestConversationThread", () => ({
  TestConversationThread: ({
    conversation,
  }: {
    conversation: { id: string };
  }) => <div>Teste isolado {conversation.id}</div>,
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

const WORKSPACE = {
  published: AGENT,
  published_version: 1,
  draft_revision: 0,
  name: AGENT.name,
  instructions: AGENT.instructions,
  has_unpublished_changes: false,
};

function mockLoad(overrides?: { attached?: unknown[] }) {
  mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (!init && path.endsWith("/workspace"))
      return { ok: true, json: async () => WORKSPACE };
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

  it("mostra explicação sobre o que colocar nas instruções", async () => {
    mockLoad();

    render(<AgentDetail agentId="a1" />);

    await waitFor(() =>
      expect(
        screen.getByDisplayValue("Você é a secretária."),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText(
        /escreva como se estivesse orientando alguém novo no escritório/i,
      ),
    ).toBeInTheDocument();
  });

  it("carrega e preenche o formulário com os dados do agente", async () => {
    mockLoad();

    render(<AgentDetail agentId="a1" />);

    await waitFor(() =>
      expect(screen.getByDisplayValue("Secretária")).toBeInTheDocument(),
    );
    expect(
      screen.getByDisplayValue("Você é a secretária."),
    ).toBeInTheDocument();
  });

  it("mostra 'agente não encontrado' quando o id não existe na lista", async () => {
    mockedFetch.mockImplementation(async (path: string) => {
      if (path.endsWith("/workspace"))
        return {
          ok: false,
          status: 404,
          json: async () => ({ detail: "Agente não encontrado" }),
        };
      return { ok: true, json: async () => [] };
    });

    render(<AgentDetail agentId="inexistente" />);

    await waitFor(() =>
      expect(screen.getByText("Agente não encontrado.")).toBeInTheDocument(),
    );
  });

  it("mostra a contagem de arquivos anexados com link pra base de conhecimento", async () => {
    mockLoad({
      attached: [{ id: "f1", filename: "regimento.pdf", status: "ready" }],
    });

    render(<AgentDetail agentId="a1" />);

    await waitFor(() =>
      expect(screen.getByText(/1 arquivo anexado/)).toBeInTheDocument(),
    );
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

    await waitFor(() =>
      expect(screen.getByText(/2 arquivos anexados/)).toBeInTheDocument(),
    );
  });

  it("salva as alterações do formulário", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        return {
          ok: true,
          json: async () => ({
            ...WORKSPACE,
            name: "Nova Secretária",
            draft_revision: 1,
            has_unpublished_changes: true,
          }),
        };
      }
      if (!init && path.endsWith("/workspace"))
        return { ok: true, json: async () => WORKSPACE };
      return { ok: true, json: async () => [] };
    });

    render(<AgentDetail agentId="a1" />);
    await waitFor(() =>
      expect(screen.getByDisplayValue("Secretária")).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByDisplayValue("Secretária"), {
      target: { value: "Nova Secretária" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Salvar rascunho" }));

    await waitFor(() =>
      expect(mockedFetch).toHaveBeenCalledWith(
        "agents/a1/draft",
        expect.objectContaining({ method: "PATCH" }),
      ),
    );
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Rascunho salvo. O atendimento ainda usa a versão publicada.",
      ),
    );
    expect(
      screen.queryByText("Há alterações não salvas."),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Salvar rascunho" }),
    ).toBeDisabled();
  });

  it("publica somente depois de confirmação explícita", async () => {
    const draft = {
      ...WORKSPACE,
      name: "Novo nome",
      has_unpublished_changes: true,
      draft_revision: 3,
    };
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path.endsWith("/workspace"))
        return { ok: true, json: async () => draft };
      if (path.endsWith("/publish") && init?.method === "POST")
        return {
          ok: true,
          json: async () => ({
            ...draft,
            published: { ...AGENT, name: "Novo nome" },
            published_version: 2,
            draft_revision: 4,
            has_unpublished_changes: false,
          }),
        };
      return { ok: true, json: async () => [] };
    });
    render(<AgentDetail agentId="a1" />);
    await screen.findByDisplayValue("Novo nome");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Secretária",
    );
    fireEvent.click(screen.getByRole("button", { name: "Publicar versão" }));
    expect(
      mockedFetch.mock.calls.some(([path]) => path.endsWith("/publish")),
    ).toBe(false);
    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar publicação" }),
    );
    await screen.findByText("Versão publicada com sucesso.");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Novo nome",
    );
    expect(mockedFetch).toHaveBeenCalledWith(
      "agents/a1/publish",
      expect.objectContaining({
        body: JSON.stringify({ description: "", expected_revision: 3 }),
      }),
    );
  });

  it("preserva texto digitado quando outra sessão altera o rascunho", async () => {
    mockLoad();
    render(<AgentDetail agentId="a1" />);
    const name = await screen.findByLabelText("Nome");
    fireEvent.change(name, { target: { value: "Meu texto" } });
    mockedFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({
        detail: "Alterado em outra sessão. Copie seu texto e recarregue.",
      }),
    });
    fireEvent.click(screen.getByRole("button", { name: "Salvar rascunho" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Alterado em outra sessão",
    );
    expect(screen.getByDisplayValue("Meu texto")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Publicar versão" }),
    ).toBeDisabled();
  });

  it("exige salvar antes de testar e mostra aviso de créditos", async () => {
    mockLoad();
    render(<AgentDetail agentId="a1" />);
    const name = await screen.findByLabelText("Nome");
    fireEvent.change(name, { target: { value: "Não salvo" } });
    fireEvent.click(screen.getByRole("button", { name: "Testar" }));
    expect(
      screen.getByText(/Os testes consomem créditos do escritório/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Iniciar teste do rascunho" }),
    ).toBeDisabled();
  });

  it("inicia teste isolado com a revisão salva e permite novo teste", async () => {
    mockLoad();
    render(<AgentDetail agentId="a1" />);
    await screen.findByLabelText("Nome");
    fireEvent.click(screen.getByRole("button", { name: "Testar" }));
    mockedFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: "isolado-1", is_test: true }),
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Iniciar teste do rascunho" }),
    );
    await screen.findByText("Teste isolado isolado-1");
    expect(mockedFetch).toHaveBeenCalledWith(
      "agents/a1/tests",
      expect.objectContaining({
        body: JSON.stringify({ expected_revision: 0 }),
      }),
    );
    mockedFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: "isolado-2", is_test: true }),
    });
    fireEvent.click(screen.getByRole("button", { name: "Iniciar novo teste" }));
    await screen.findByText("Teste isolado isolado-2");
    expect(
      screen.queryByText("Teste isolado isolado-1"),
    ).not.toBeInTheDocument();
  });

  it("compara versões e confirma substituição do rascunho sem publicar", async () => {
    mockedFetch.mockImplementation(async (path: string) => {
      if (path.endsWith("/workspace"))
        return { ok: true, json: async () => WORKSPACE };
      if (path.endsWith("/versions"))
        return {
          ok: true,
          json: async () => [
            {
              number: 1,
              name: "Nome antigo",
              instructions: "Instrução antiga",
              author_name: "Ana",
              created_at: AGENT.created_at,
              description: "Primeira",
            },
          ],
        };
      if (path.endsWith("/restore"))
        return {
          ok: true,
          json: async () => ({
            ...WORKSPACE,
            name: "Nome antigo",
            instructions: "Instrução antiga",
            draft_revision: 1,
            has_unpublished_changes: true,
          }),
        };
      return { ok: true, json: async () => [] };
    });
    render(<AgentDetail agentId="a1" />);
    await screen.findByLabelText("Nome");
    fireEvent.click(screen.getByRole("button", { name: "Versões" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Versão 1 — publicada" }),
    );
    expect(screen.getByText("Instrução antiga")).toBeInTheDocument();
    expect(screen.getByText("Você é a secretária.")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Restaurar como rascunho" }),
    );
    expect(
      mockedFetch.mock.calls.some(([path]) => path.endsWith("/restore")),
    ).toBe(false);
    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar restauração" }),
    );
    await screen.findByDisplayValue("Nome antigo");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Secretária",
    );
    expect(
      mockedFetch.mock.calls.some(([path]) => path.endsWith("/publish")),
    ).toBe(false);
  });

  it("preserva o texto e mantém o aviso quando o salvamento falha", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        return {
          ok: false,
          json: async () => ({ detail: "Falha ao salvar." }),
        };
      }
      if (!init && path.endsWith("/workspace"))
        return { ok: true, json: async () => WORKSPACE };
      return { ok: true, json: async () => [] };
    });
    render(<AgentDetail agentId="a1" />);
    const nameInput = await screen.findByDisplayValue("Secretária");
    fireEvent.change(nameInput, { target: { value: "Nome ajustado" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvar rascunho" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Falha ao salvar.",
    );
    expect(screen.getByDisplayValue("Nome ajustado")).toBeInTheDocument();
    expect(screen.getByText("Há alterações não salvas.")).toBeInTheDocument();
  });

  it("permite descartar alterações não salvas", async () => {
    mockLoad();
    render(<AgentDetail agentId="a1" />);
    const nameInput = await screen.findByDisplayValue("Secretária");
    fireEvent.change(nameInput, { target: { value: "Outro nome" } });
    expect(screen.getByText("Há alterações não salvas.")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Descartar alterações" }),
    );
    expect(screen.getByDisplayValue("Secretária")).toBeInTheDocument();
    expect(
      screen.queryByText("Há alterações não salvas."),
    ).not.toBeInTheDocument();
  });
});
