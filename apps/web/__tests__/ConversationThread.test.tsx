import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationThread } from "@/components/ConversationThread";
import { backendFetch } from "@/lib/client-api";
import type { Conversation, Message } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const backendFetchMock = vi.mocked(backendFetch);

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function conversation(state: "agent" | "human"): Conversation {
  return {
    id: "c1",
    contact_phone_number: "5511999998888",
    state,
    last_message_at: null,
    created_at: new Date().toISOString(),
  };
}

const messages: Message[] = [
  {
    id: "m2",
    sender_type: "agent",
    content: "Posso ajudar com o condomínio.",
    media_url: null,
    media_type: null,
    created_at: new Date().toISOString(),
  },
  {
    id: "m1",
    sender_type: "contact",
    content: "Olá, tenho uma dúvida.",
    media_url: null,
    media_type: null,
    created_at: new Date().toISOString(),
  },
];

beforeEach(() => {
  backendFetchMock.mockReset();
});

describe("ConversationThread", () => {
  it("carrega e exibe as mensagens em ordem de leitura", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse(messages));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Olá, tenho uma dúvida.")).toBeInTheDocument();
    });
    expect(screen.getByText("Posso ajudar com o condomínio.")).toBeInTheDocument();
    expect(screen.getByText("Agente")).toBeInTheDocument();
  });

  it("em modo agente, o campo de resposta fica desativado com orientação", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    expect(screen.getByLabelText("Resposta")).toBeDisabled();
    expect(screen.getByText("Assuma a conversa para responder.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Assumir conversa" })).toBeInTheDocument();
  });

  it("assumir conversa envia PATCH e propaga a conversa atualizada", async () => {
    const updated = conversation("human");
    backendFetchMock.mockImplementation(async (path, init) => {
      if (init?.method === "PATCH") {
        return jsonResponse(updated);
      }
      return jsonResponse([]);
    });
    const onConversationUpdate = vi.fn();

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={onConversationUpdate}
        pollMs={0}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Assumir conversa" }));

    await waitFor(() => {
      expect(onConversationUpdate).toHaveBeenCalledWith(updated);
    });
    expect(backendFetchMock).toHaveBeenCalledWith(
      "conversations/c1",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ state: "human" }) }),
    );
  });

  it("em modo manual, mostra o carimbo e envia mensagem", async () => {
    const sent: Message = {
      id: "m3",
      sender_type: "human",
      content: "Bom dia, aqui é o advogado.",
      media_url: null,
      media_type: null,
      created_at: new Date().toISOString(),
    };
    backendFetchMock.mockImplementation(async (path, init) => {
      if (init?.method === "POST") {
        return jsonResponse(sent, 201);
      }
      return jsonResponse([]);
    });

    render(
      <ConversationThread
        conversation={conversation("human")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    expect(screen.getByText("Atendimento manual")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Resposta"), {
      target: { value: "Bom dia, aqui é o advogado." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));

    await waitFor(() => {
      expect(screen.getByText("Bom dia, aqui é o advogado.")).toBeInTheDocument();
    });
    expect(backendFetchMock).toHaveBeenCalledWith(
      "conversations/c1/messages",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("mostra erro quando o WhatsApp não recebe a mensagem", async () => {
    backendFetchMock.mockImplementation(async (path, init) => {
      if (init?.method === "POST") {
        return jsonResponse({ detail: "erro" }, 502);
      }
      return jsonResponse([]);
    });

    render(
      <ConversationThread
        conversation={conversation("human")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    fireEvent.change(screen.getByLabelText("Resposta"), { target: { value: "oi" } });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));

    await waitFor(() => {
      expect(
        screen.getByText("O WhatsApp não recebeu a mensagem. Tente novamente."),
      ).toBeInTheDocument();
    });
  });
});
