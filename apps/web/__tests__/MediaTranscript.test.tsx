import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MediaTranscript } from "@/components/MediaTranscript";

describe("MediaTranscript", () => {
  it("mostra a transcrição e destaca trechos incertos", () => {
    render(
      <MediaTranscript
        media={{ kind: "audio", status: "done", error_code: null, text: "CPF [?123?] ok" }}
      />,
    );
    expect(screen.getByText("Transcrição automática")).toBeInTheDocument();
    expect(screen.getByText("123")).toHaveAttribute("title", "Trecho incerto");
  });

  it("mostra o motivo quando o áudio não foi transcrito", () => {
    render(
      <MediaTranscript
        media={{ kind: "audio", status: "failed", error_code: "too_long", text: null }}
      />,
    );
    expect(screen.getByText(/Áudio não transcrito/)).toHaveTextContent("passa do limite de duração");
  });

  it("indica processamento em andamento", () => {
    render(
      <MediaTranscript
        media={{ kind: "audio", status: "processing", error_code: null, text: null }}
      />,
    );
    expect(screen.getByText("Transcrevendo áudio…")).toBeInTheDocument();
  });

  it("não renderiza nada para mídia que não é áudio", () => {
    const { container } = render(
      <MediaTranscript
        media={{ kind: "document", status: "done", error_code: null, text: "x" }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
