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

function conversation(
  state: "agent" | "human",
  summary: string | null = null,
  summaryGeneratedAt: string | null = null,
): Conversation {
  return {
    id: "c1",
    contact_phone_number: "5511999998888",
    state,
    last_message_at: null,
    created_at: new Date().toISOString(),
    summary,
    summary_generated_at: summaryGeneratedAt,
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

  it("em modo agente, o campo de resposta fica desativado e o switch está ligado", async () => {
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
    const switchControl = screen.getByRole("switch", { name: "IA respondendo" });
    expect(switchControl).toHaveAttribute("aria-checked", "true");
  });

  it("em modo manual, o switch aparece desligado", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("human")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    const switchControl = screen.getByRole("switch", { name: "IA respondendo" });
    expect(switchControl).toHaveAttribute("aria-checked", "false");
  });

  it("acionar o switch envia PATCH e propaga a conversa atualizada", async () => {
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

    fireEvent.click(screen.getByRole("switch", { name: "IA respondendo" }));

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

  it("sem resumo, mostra o estado vazio e o botão 'Resumir conversa'", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse(messages));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    fireEvent.click(screen.getByText("Resumo da conversa"));

    expect(screen.getByText("Nenhum resumo gerado ainda.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resumir conversa" })).toBeInTheDocument();
  });

  it("com resumo existente, começa expandido com o botão 'Atualizar resumo'", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse(messages));

    render(
      <ConversationThread
        conversation={conversation("agent", "Resumo anterior.", new Date().toISOString())}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    expect(screen.getByText("Resumo anterior.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Atualizar resumo" })).toBeInTheDocument();
  });

  it("gera o resumo com sucesso e propaga a conversa atualizada", async () => {
    const updated = conversation("agent", "Resumo novo gerado.", new Date().toISOString());
    backendFetchMock.mockImplementation(async (path, init) => {
      if (init?.method === "POST" && path === "conversations/c1/summary") {
        return jsonResponse(updated);
      }
      return jsonResponse(messages);
    });
    const onConversationUpdate = vi.fn();

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={onConversationUpdate}
        pollMs={0}
      />,
    );

    fireEvent.click(screen.getByText("Resumo da conversa"));
    fireEvent.click(screen.getByRole("button", { name: "Resumir conversa" }));

    await waitFor(() => {
      expect(onConversationUpdate).toHaveBeenCalledWith(updated);
    });
  });

  it("mostra aviso de saldo esgotado (402) com link para /creditos", async () => {
    backendFetchMock.mockImplementation(async (path, init) => {
      if (init?.method === "POST" && path === "conversations/c1/summary") {
        return jsonResponse({ detail: "Saldo esgotado" }, 402);
      }
      return jsonResponse(messages);
    });

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    fireEvent.click(screen.getByText("Resumo da conversa"));
    fireEvent.click(screen.getByRole("button", { name: "Resumir conversa" }));

    await waitFor(() => {
      expect(
        screen.getByText("Saldo de créditos esgotado — não é possível gerar o resumo."),
      ).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: "Comprar créditos" })).toHaveAttribute(
      "href",
      "/creditos",
    );
  });

  it("mostra erro genérico quando a chamada de resumo rejeita (falha de rede)", async () => {
    backendFetchMock.mockImplementation(async (path, init) => {
      if (init?.method === "POST" && path === "conversations/c1/summary") {
        throw new Error("network error");
      }
      return jsonResponse(messages);
    });

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    fireEvent.click(screen.getByText("Resumo da conversa"));
    fireEvent.click(screen.getByRole("button", { name: "Resumir conversa" }));

    await waitFor(() => {
      expect(
        screen.getByText("Não foi possível gerar o resumo. Tente novamente."),
      ).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Resumir conversa" })).not.toBeDisabled();
  });

  it("desabilita o botão de resumo quando a conversa não tem mensagens", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    fireEvent.click(screen.getByText("Resumo da conversa"));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Resumir conversa" })).toBeDisabled();
    });
  });
});
