import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationsPanel } from "@/components/ConversationsPanel";
import { backendFetch } from "@/lib/client-api";
import type { Conversation } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const backendFetchMock = vi.mocked(backendFetch);

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function conversation(id: string, isTest: boolean): Conversation {
  return {
    id,
    contact_phone_number: isTest ? `teste-${id}` : "5511999998888",
    state: "agent",
    is_test: isTest,
    last_message_at: null,
    created_at: new Date().toISOString(),
    summary: null,
    summary_generated_at: null,
    end_customer_billing_exempt: false,
    end_customer_billing_enabled: false,
  };
}

beforeEach(() => {
  backendFetchMock.mockReset();
});

describe("ConversationsPanel — abas", () => {
  it("aba padrão busca origin=real e a de testes origin=test", async () => {
    backendFetchMock.mockImplementation(async (path: string) => {
      if (String(path).includes("origin=test")) {
        return jsonResponse([conversation("t1", true)]);
      }
      return jsonResponse([conversation("r1", false)]);
    });

    render(<ConversationsPanel pollMs={0} />);

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(([p]) => String(p).includes("origin=real")),
      ).toBe(true),
    );

    fireEvent.click(screen.getByRole("button", { name: "Testes" }));

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(([p]) => String(p).includes("origin=test")),
      ).toBe(true),
    );
    expect(screen.getByText("Nova conversa de teste")).toBeInTheDocument();
  });

  it("nova conversa de teste cria e seleciona", async () => {
    const created = conversation("novo", true);
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (String(path) === "test-conversations" && init?.method === "POST") {
        return jsonResponse(created, 201);
      }
      return jsonResponse([]);
    });

    render(<ConversationsPanel pollMs={0} />);

    fireEvent.click(screen.getByRole("button", { name: "Testes" }));
    fireEvent.click(await screen.findByText("Nova conversa de teste"));

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(
          ([p, init]) => String(p) === "test-conversations" && init?.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("initialOrigin=test abre direto na aba Testes", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(<ConversationsPanel pollMs={0} initialOrigin="test" />);

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(([p]) => String(p).includes("origin=test")),
      ).toBe(true),
    );
    expect(screen.getByText("Nova conversa de teste")).toBeInTheDocument();
  });
});
