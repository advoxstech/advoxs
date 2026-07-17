import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationsUsageReport } from "@/components/ConversationsUsageReport";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("ConversationsUsageReport", () => {
  it("busca o período default de 30 dias ao carregar", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => [] });

    render(<ConversationsUsageReport />);

    await waitFor(() =>
      expect(
        mockedFetch.mock.calls.some(([path]) =>
          String(path).startsWith("conversations/usage?from="),
        ),
      ).toBe(true),
    );
  });

  it("mostra estado vazio quando não há consumo no período", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => [] });

    render(<ConversationsUsageReport />);

    await waitFor(() =>
      expect(screen.getByText("Nenhum consumo no período selecionado.")).toBeInTheDocument(),
    );
  });

  it("lista as conversas com créditos formatados e badge de teste", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          conversation_id: "c1",
          contact_phone_number: "5511999998888",
          is_test: false,
          credits_consumed: 12.5,
          billed_responses: 3,
          last_message_at: "2026-07-15T10:00:00Z",
        },
        {
          conversation_id: "c2",
          contact_phone_number: "teste-abc123def456",
          is_test: true,
          credits_consumed: 2,
          billed_responses: 1,
          last_message_at: "2026-07-10T10:00:00Z",
        },
      ],
    });

    render(<ConversationsUsageReport />);

    await waitFor(() => expect(screen.getByText("12,5")).toBeInTheDocument());
    expect(screen.getByText("teste")).toBeInTheDocument();
  });

  it("trocar para o preset de 7 dias refaz a busca com o novo intervalo", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => [] });

    render(<ConversationsUsageReport />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    mockedFetch.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "7 dias" }));

    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
  });

  it("nunca menciona tokens na tela", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          conversation_id: "c1",
          contact_phone_number: "5511999998888",
          is_test: false,
          credits_consumed: 5,
          billed_responses: 2,
          last_message_at: "2026-07-15T10:00:00Z",
        },
      ],
    });

    const { container } = render(<ConversationsUsageReport />);

    await waitFor(() => expect(screen.getByText("5")).toBeInTheDocument());
    expect(container.textContent?.toLowerCase()).not.toContain("token");
  });
});
