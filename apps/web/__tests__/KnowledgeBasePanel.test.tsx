import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { KnowledgeBasePanel } from "@/components/KnowledgeBasePanel";

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

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => files,
  })),
}));

describe("KnowledgeBasePanel", () => {
  it("lista os arquivos com status", async () => {
    render(<KnowledgeBasePanel pollMs={0} />);

    await waitFor(() => expect(screen.getByText("regimento.pdf")).toBeInTheDocument());
    expect(screen.getByText("contrato.docx")).toBeInTheDocument();
    expect(screen.getByText(/pronto/i)).toBeInTheDocument();
    expect(screen.getByText(/Falha na ingestão/)).toBeInTheDocument();
  });
});
