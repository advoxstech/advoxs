import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { KnowledgeBasePanel } from "@/components/KnowledgeBasePanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const agents = [
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

const files = [
  {
    id: "f1",
    filename: "regimento.pdf",
    size_bytes: 1048576,
    mime_type: "application/pdf",
    status: "ready",
    error_message: null,
    uploaded_at: "2026-07-08T12:00:00Z",
    agent_ids: ["a1"],
  },
  {
    id: "f2",
    filename: "contrato.docx",
    size_bytes: 2048,
    mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    status: "error",
    error_message: "Falha na ingestão (HTTP 400)",
    uploaded_at: "2026-07-08T11:00:00Z",
    agent_ids: ["a1", "a2"],
  },
];

function mockRouting(postHandler?: (path: string, init: RequestInit) => unknown) {
  mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "agents") return { ok: true, status: 200, json: async () => agents };
    if (path === "knowledge-base/files" && (!init || !init.method)) {
      return { ok: true, status: 200, json: async () => files };
    }
    if (init?.method === "POST" || init?.method === "DELETE") {
      return postHandler
        ? postHandler(path, init)
        : { ok: true, status: 200, json: async () => null };
    }
    return { ok: true, status: 200, json: async () => null };
  });
}

describe("KnowledgeBasePanel", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
    window.history.pushState({}, "", "/base-de-conhecimento");
  });

  it("renderiza 1 pasta por agente, incluindo agente sem arquivos", async () => {
    mockRouting();

    render(<KnowledgeBasePanel pollMs={0} />);

    await waitFor(() => expect(screen.getByText("Secretária")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Condominial/ })).toBeInTheDocument();
    expect(screen.getByText("[2 arquivos]")).toBeInTheDocument();
    expect(screen.getByText("[1 arquivo]")).toBeInTheDocument();
  });

  it("um arquivo em 2 agentes aparece nas 2 pastas", async () => {
    mockRouting();

    render(<KnowledgeBasePanel pollMs={0} />);

    // Secretária (ponto de entrada) começa expandida por padrão.
    await waitFor(() => expect(screen.getByText("contrato.docx")).toBeInTheDocument());
    expect(screen.getByText("regimento.pdf")).toBeInTheDocument();

    // Condominial começa recolhida — expande pra confirmar que o mesmo
    // arquivo também aparece lá.
    fireEvent.click(screen.getByRole("button", { name: /Condominial/ }));
    await waitFor(() => expect(screen.getAllByText("contrato.docx")).toHaveLength(2));
    expect(screen.queryAllByText("regimento.pdf")).toHaveLength(1);
  });

  it("pré-expande a pasta vinda da URL (?agent_id=)", async () => {
    window.history.pushState({}, "", "/base-de-conhecimento?agent_id=a2");
    mockRouting();

    render(<KnowledgeBasePanel pollMs={0} />);

    await waitFor(() => expect(screen.getByText("contrato.docx")).toBeInTheDocument());
    expect(screen.queryByText("regimento.pdf")).not.toBeInTheDocument();
  });

  it("envia o agent_id certo no upload de cada pasta", async () => {
    let capturedForm: FormData | null = null;
    mockRouting((_path, init) => {
      capturedForm = init.body as FormData;
      return { ok: true, status: 202, json: async () => files[0] };
    });

    render(<KnowledgeBasePanel pollMs={0} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Condominial/ })).toBeInTheDocument());

    const file = new File(["conteudo"], "novo.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Enviar arquivo para Condominial"), {
      target: { files: [file] },
    });

    await waitFor(() => expect(capturedForm).not.toBeNull());
    expect(capturedForm!.get("agent_id")).toBe("a2");
  });

  it("anexa um arquivo já existente a outro agente pelo seletor inline", async () => {
    let capturedPath = "";
    let capturedBody = "";
    mockRouting((path, init) => {
      capturedPath = path;
      capturedBody = init.body as string;
      return { ok: true, status: 201, json: async () => ({ knowledge_base_file_id: "f1" }) };
    });

    render(<KnowledgeBasePanel pollMs={0} />);
    await waitFor(() => expect(screen.getByText("regimento.pdf")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Anexar regimento.pdf a outro agente"), {
      target: { value: "a2" },
    });

    await waitFor(() => expect(capturedPath).toBe("agents/a2/knowledge-base-files"));
    expect(JSON.parse(capturedBody).knowledge_base_file_id).toBe("f1");
  });

  it("desabilita 'desanexar' quando o arquivo só tem 1 agente", async () => {
    mockRouting();

    render(<KnowledgeBasePanel pollMs={0} />);
    await waitFor(() => expect(screen.getByText("regimento.pdf")).toBeInTheDocument());

    expect(screen.getByLabelText("Desanexar regimento.pdf deste agente")).toBeDisabled();
    expect(screen.getByLabelText("Desanexar contrato.docx deste agente")).not.toBeDisabled();
  });

  it("exclui um arquivo após confirmação", async () => {
    // handleDelete chama load() de novo após o DELETE — o mock precisa
    // simular o backend removendo o arquivo, senão o GET seguinte devolve
    // a mesma lista estática e o teste nunca vê o arquivo desaparecer.
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    let deleted = false;
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "agents") return { ok: true, status: 200, json: async () => agents };
      if (path === "knowledge-base/files/f1" && init?.method === "DELETE") {
        deleted = true;
        return { ok: true, status: 204, json: async () => null };
      }
      if (path === "knowledge-base/files") {
        return { ok: true, status: 200, json: async () => (deleted ? files.slice(1) : files) };
      }
      return { ok: true, status: 200, json: async () => null };
    });

    render(<KnowledgeBasePanel pollMs={0} />);
    await waitFor(() => expect(screen.getByText("regimento.pdf")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Excluir regimento.pdf"));

    await waitFor(() => expect(screen.queryByText("regimento.pdf")).not.toBeInTheDocument());
    confirmSpy.mockRestore();
  });
});
