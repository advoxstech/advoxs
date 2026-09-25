import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { ResponseSources } from "@/components/ResponseSources";
import type { Message, ResponseEvidence } from "@/lib/types";

const source = {
  document_id: "doc-1", filename: "Atendimento.pdf", chunk_id: "chunk-1",
  excerpt: "Consulta de 45 minutos.", page: null,
};
function message(evidence?: ResponseEvidence): Message {
  return {
    id: "m1", sender_type: "agent", content: "Resposta", media_url: null,
    media_type: null, delivery_status: "sent", created_at: "2026-09-24T10:00:00Z",
    response_sources: evidence,
  };
}

afterEach(() => vi.unstubAllGlobals());

it("keeps old messages distinct from answers without sources", () => {
  render(<ResponseSources message={message()} />);
  expect(screen.getByText("Fontes não registradas para esta mensagem.")).toBeInTheDocument();
});

it.each([
  ["no_documents", "o agente não tem documentos vinculados"],
  ["not_searched", "Base não consultada"],
  ["empty", "Nenhum conteúdo encontrado"],
  ["not_used", "nenhum foi indicado como fonte"],
  ["unavailable", "Base temporariamente indisponível"],
] as const)("explains %s", (status, text) => {
  render(<ResponseSources message={message({ status, sources: [], search_failed: false })} />);
  expect(screen.getByText(new RegExp(text))).toBeInTheDocument();
});

it("groups document count, preserves excerpts and does not invent pages", () => {
  render(<ResponseSources message={message({
    status: "referenced", sources: [source, { ...source, chunk_id: "chunk-2", page: 3 }],
    search_failed: true,
  })} />);
  const summary = screen.getByText("Fontes da resposta · 1 documento");
  expect(summary.closest("details")).not.toHaveAttribute("open");
  fireEvent.click(summary);
  expect(summary.closest("details")).toHaveAttribute("open");
  expect(screen.getAllByText("Consulta de 45 minutos.")).toHaveLength(2);
  expect(screen.getAllByText(/Página/)).toHaveLength(1);
  expect(screen.getByText("Página 3")).toBeInTheDocument();
  expect(screen.getByText(/Uma das consultas/)).toBeInTheDocument();
});

it("keeps the snapshot readable when the original was removed", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 404 });
  vi.stubGlobal("fetch", fetchMock);
  render(<ResponseSources message={message({ status: "referenced", sources: [source], search_failed: false })} />);
  fireEvent.click(screen.getByText("Fontes da resposta · 1 documento"));
  fireEvent.click(screen.getByRole("button", { name: "Baixar documento original" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("O trecho utilizado foi preservado"));
  expect(fetchMock).toHaveBeenCalledWith("/api/backend/knowledge-base/files/doc-1/content", { cache: "no-store" });
  expect(screen.getByText(source.excerpt)).toBeInTheDocument();
});

it("offers a new attempt after a network failure", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  render(<ResponseSources message={message({ status: "referenced", sources: [source], search_failed: false })} />);
  fireEvent.click(screen.getByText("Fontes da resposta · 1 documento"));
  fireEvent.click(screen.getByRole("button"));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Verifique sua conexão"));
  expect(screen.getByRole("button")).not.toBeDisabled();
});

it("does not add source controls to human or contact messages", () => {
  const { container } = render(<ResponseSources message={{ ...message(), sender_type: "human" }} />);
  expect(container).toBeEmptyDOMElement();
});
