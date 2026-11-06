import { describe, expect, it } from "vitest";

import { type KbFile, partialReadingText } from "@/components/AgentFolder";

const file: KbFile = {
  id: "f1",
  filename: "processo.pdf",
  size_bytes: 1000,
  mime_type: "application/pdf",
  status: "ready",
  error_message: null,
  category: null,
  uploaded_at: "2026-11-05T10:00:00Z",
  agent_ids: [],
};

describe("partialReadingText", () => {
  it("is empty when every page was read", () => {
    expect(partialReadingText(file)).toBeNull();
    expect(partialReadingText({ ...file, partial_pages: [] })).toBeNull();
  });

  it("lists the pages that were not read", () => {
    expect(partialReadingText({ ...file, pages_total: 50, partial_pages: [12, 18, 30] })).toBe(
      "3 páginas de 50 não foram lidas ou ficaram com leitura ruim (p. 12, 18, 30). " +
        "Envie uma digitalização mais nítida dessas páginas para o agente consultá-las.",
    );
  });

  it("uses singular for one page", () => {
    expect(partialReadingText({ ...file, pages_total: 5, partial_pages: [2] })).toMatch(
      /^1 página de 5 não foi lida ou ficou com leitura ruim \(p\. 2\)/,
    );
  });

  it("ignores files that are not ready", () => {
    expect(partialReadingText({ ...file, status: "processing", partial_pages: [2] })).toBeNull();
  });
});
