import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { KnowledgeBasePanel } from "@/components/KnowledgeBasePanel";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const files = [
  {
    id: "f1",
    filename: "regimento.pdf",
    size_bytes: 1048576,
    mime_type: "application/pdf",
    status: "ready",
    error_message: null,
    uploaded_at: "2026-07-08T12:00:00Z",
  },
  {
    id: "f2",
    filename: "contrato.docx",
    size_bytes: 2048,
    mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    status: "error",
    error_message: "Falha na ingestão (HTTP 400)",
    uploaded_at: "2026-07-08T11:00:00Z",
  },
];

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

function mockRouting(uploadHandler?: (init: RequestInit) => unknown) {
  mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === "agents") return { ok: true, status: 200, json: async () => agents };
    if (path === "knowledge-base/files" && init?.method === "POST") {
      return uploadHandler
        ? uploadHandler(init)
        : { ok: true, status: 202, json: async () => files[0] };
    }
    if (path === "knowledge-base/files") return { ok: true, status: 200, json: async () => files };
    return { ok: true, status: 200, json: async () => null };
  });
}

describe("KnowledgeBasePanel", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
    window.history.pushState({}, "", "/base-de-conhecimento");
  });

  it("lista os arquivos com status", async () => {
    mockRouting();

    render(<KnowledgeBasePanel pollMs={0} />);

    await waitFor(() => expect(screen.getByText("regimento.pdf")).toBeInTheDocument());
    expect(screen.getByText("contrato.docx")).toBeInTheDocument();
    expect(screen.getByText(/pronto/i)).toBeInTheDocument();
    expect(screen.getByText(/Falha na ingestão/)).toBeInTheDocument();
  });

  it("pré-seleciona o agente ponto de entrada por padrão", async () => {
    mockRouting();

    render(<KnowledgeBasePanel pollMs={0} />);

    await waitFor(() => expect(screen.getByLabelText("Agente de destino")).toHaveValue("a1"));
  });

  it("pré-seleciona o agente vindo da URL (?agent_id=)", async () => {
    window.history.pushState({}, "", "/base-de-conhecimento?agent_id=a2");
    mockRouting();

    render(<KnowledgeBasePanel pollMs={0} />);

    await waitFor(() => expect(screen.getByLabelText("Agente de destino")).toHaveValue("a2"));
  });

  it("envia o agent_id selecionado no FormData do upload", async () => {
    let capturedForm: FormData | null = null;
    mockRouting((init) => {
      capturedForm = init.body as FormData;
      return { ok: true, status: 202, json: async () => files[0] };
    });

    render(<KnowledgeBasePanel pollMs={0} />);
    await waitFor(() => expect(screen.getByLabelText("Agente de destino")).toHaveValue("a1"));

    fireEvent.change(screen.getByLabelText("Agente de destino"), { target: { value: "a2" } });
    const file = new File(["conteudo"], "novo.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Enviar arquivo"), { target: { files: [file] } });

    await waitFor(() => expect(capturedForm).not.toBeNull());
    expect(capturedForm!.get("agent_id")).toBe("a2");
  });

  it("mostra erro se tentar enviar sem nenhum agente disponível", async () => {
    mockedFetch.mockImplementation(async (path: string) => {
      if (path === "agents") return { ok: true, status: 200, json: async () => [] };
      if (path === "knowledge-base/files") return { ok: true, status: 200, json: async () => [] };
      return { ok: true, status: 200, json: async () => null };
    });

    render(<KnowledgeBasePanel pollMs={0} />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalledWith("agents"));

    const file = new File(["conteudo"], "novo.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Enviar arquivo"), { target: { files: [file] } });

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/escolha o agente/i));
  });
});
