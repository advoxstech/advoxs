import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TestConversationThread } from "@/components/TestConversationThread";
import { backendFetch } from "@/lib/client-api";
import type { Conversation, Message } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const backendFetchMock = vi.mocked(backendFetch);

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

const conversation: Conversation = {
  id: "t1",
  contact_phone_number: "teste-abc123",
  state: "agent",
  is_test: true,
  last_message_at: null,
  created_at: new Date().toISOString(),
  summary: null,
  summary_generated_at: null,
};

function message(id: string, sender: Message["sender_type"], content: string): Message {
  return {
    id,
    sender_type: sender,
    content,
    media_url: null,
    media_type: null,
    delivery_status: null,
    created_at: new Date().toISOString(),
  };
}

beforeEach(() => {
  backendFetchMock.mockReset();
});

describe("TestConversationThread", () => {
  it("envia mensagem e renderiza a resposta do agente", async () => {
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (String(path).endsWith("/test-messages") && init?.method === "POST") {
        return jsonResponse(
          {
            messages: [
              message("m1", "contact", "olá"),
              message("m2", "agent", "Oi! Como posso ajudar?"),
            ],
            grouped: false,
          },
          201,
        );
      }
      return jsonResponse([]);
    });

    render(<TestConversationThread conversation={conversation} onDeleted={vi.fn()} />);

    const input = await screen.findByLabelText("Mensagem de teste");
    fireEvent.change(input, { target: { value: "olá" } });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));

    await waitFor(() =>
      expect(screen.getByText("Oi! Como posso ajudar?")).toBeInTheDocument(),
    );
    expect(screen.getByText("olá")).toBeInTheDocument();
  });

  it("mostra aviso de saldo esgotado no 402", async () => {
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return jsonResponse({ detail: "Saldo esgotado" }, 402);
      }
      return jsonResponse([]);
    });

    render(<TestConversationThread conversation={conversation} onDeleted={vi.fn()} />);

    const input = await screen.findByLabelText("Mensagem de teste");
    fireEvent.change(input, { target: { value: "olá" } });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));

    await waitFor(() =>
      expect(screen.getByText(/Saldo de créditos esgotado/)).toBeInTheDocument(),
    );
  });

  it("exclui a conversa com confirmação", async () => {
    const onDeleted = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "DELETE") {
        return jsonResponse(null, 204);
      }
      return jsonResponse([]);
    });

    render(<TestConversationThread conversation={conversation} onDeleted={onDeleted} />);

    fireEvent.click(await screen.findByRole("button", { name: "Excluir conversa" }));

    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
  });

  it("não duplica a mensagem do contato quando o polling já a trouxe antes do POST resolver", async () => {
    // Corrida real: a mensagem do contato é commitada antes da chamada ao
    // agente (que pode levar >4s), então o poll a exibe primeiro; o retorno
    // do POST inclui a mesma mensagem e não pode duplicá-la.
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (String(path).endsWith("/test-messages") && init?.method === "POST") {
        return jsonResponse(
          {
            messages: [
              message("m1", "contact", "olá"),
              message("m2", "agent", "Oi! Como posso ajudar?"),
            ],
            grouped: false,
          },
          201,
        );
      }
      // GET messages: o poll já trouxe a mensagem do contato
      return jsonResponse([message("m1", "contact", "olá")]);
    });

    render(<TestConversationThread conversation={conversation} onDeleted={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("olá")).toBeInTheDocument());

    const input = screen.getByLabelText("Mensagem de teste");
    fireEvent.change(input, { target: { value: "olá" } });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));

    await waitFor(() =>
      expect(screen.getByText("Oi! Como posso ajudar?")).toBeInTheDocument(),
    );
    expect(screen.getAllByText("olá")).toHaveLength(1);
  });
});
