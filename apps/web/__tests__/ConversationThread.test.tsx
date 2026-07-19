import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
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
    is_test: false,
    last_message_at: null,
    created_at: new Date().toISOString(),
    summary,
    summary_generated_at: summaryGeneratedAt,
  };
}

// Simula o pai real (ConversationsPanel): guarda a conversa em estado e
// repassa a versão atualizada de volta pro filho via onConversationUpdate,
// igual acontece no app de verdade (diferente de um vi.fn() que não
// rerenderiza a prop).
function Harness({ initial }: { initial: Conversation }) {
  const [conv, setConv] = useState(initial);
  return <ConversationThread conversation={conv} onConversationUpdate={setConv} pollMs={0} />;
}

const messages: Message[] = [
  {
    id: "m2",
    sender_type: "agent",
    content: "Posso ajudar com o condomínio.",
    media_url: null,
    media_type: null,
    delivery_status: null,
    created_at: new Date().toISOString(),
  },
  {
    id: "m1",
    sender_type: "contact",
    content: "Olá, tenho uma dúvida.",
    media_url: null,
    media_type: null,
    delivery_status: null,
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

  it("em modo agente, o campo de resposta fica habilitado e o switch está ligado", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    expect(screen.getByLabelText("Resposta")).not.toBeDisabled();
    expect(
      screen.getByText("Começar a digitar pausa a IA e você assume a conversa."),
    ).toBeInTheDocument();
    const switchControl = screen.getByRole("switch", { name: "IA respondendo" });
    expect(switchControl).toHaveAttribute("aria-checked", "true");
  });

  it("composer fica habilitado mesmo em modo agent", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={vi.fn()}
        pollMs={0}
      />,
    );

    await waitFor(() =>
      expect(screen.getByLabelText("Resposta")).not.toBeDisabled(),
    );
  });

  it("focar o composer em modo agent assume a conversa e mostra o popup", async () => {
    const onUpdate = vi.fn();
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        return jsonResponse({ ...conversation("human") });
      }
      return jsonResponse([]);
    });

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={onUpdate}
        pollMs={0}
      />,
    );

    await waitFor(() => expect(screen.getByLabelText("Resposta")).not.toBeDisabled());
    fireEvent.focus(screen.getByLabelText("Resposta"));

    await waitFor(() => expect(screen.getByText("IA pausada")).toBeInTheDocument());
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ state: "human" }));
    expect(
      backendFetchMock.mock.calls.some(
        ([path, init]) => path === "conversations/c1" && init?.method === "PATCH",
      ),
    ).toBe(true);
  });

  it("Devolver pra IA faz o PATCH de volta pra agent", async () => {
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        return jsonResponse({ ...conversation(body.state) });
      }
      return jsonResponse([]);
    });

    render(<Harness initial={conversation("agent")} />);

    await waitFor(() => expect(screen.getByLabelText("Resposta")).not.toBeDisabled());
    fireEvent.focus(screen.getByLabelText("Resposta"));
    await waitFor(() => expect(screen.getByText("IA pausada")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Devolver pra IA" }));

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(
          ([path, init]) =>
            path === "conversations/c1" &&
            init?.method === "PATCH" &&
            JSON.parse(String(init.body)).state === "agent",
        ),
      ).toBe(true),
    );
    expect(screen.queryByText("IA pausada")).not.toBeInTheDocument();
  });

  it("envia heartbeat no ciclo de polling quando em modo human", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("human")}
        onConversationUpdate={vi.fn()}
        pollMs={40}
      />,
    );

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(
          ([path, init]) =>
            path === "conversations/c1/heartbeat" && init?.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("não envia heartbeat em modo agent", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={vi.fn()}
        pollMs={40}
      />,
    );

    await waitFor(() =>
      expect(
        backendFetchMock.mock.calls.some(([path]) => String(path).includes("messages")),
      ).toBe(true),
    );
    expect(
      backendFetchMock.mock.calls.some(([path]) => String(path).includes("heartbeat")),
    ).toBe(false);
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
      delivery_status: null,
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

  it("mostra o badge 'Não entregue' quando a mensagem falhou ao entregar", async () => {
    const failedMessages: Message[] = [
      {
        id: "m3",
        sender_type: "agent",
        content: "Resposta que não chegou ao WhatsApp.",
        media_url: null,
        media_type: null,
        delivery_status: "failed",
        created_at: new Date().toISOString(),
      },
    ];
    backendFetchMock.mockResolvedValue(jsonResponse(failedMessages));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Não entregue")).toBeInTheDocument();
    });
  });

  it("não mostra o badge quando a mensagem foi entregue", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse(messages));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Posso ajudar com o condomínio.")).toBeInTheDocument();
    });
    expect(screen.queryByText("Não entregue")).not.toBeInTheDocument();
  });

  it("exclui a conversa com confirmação e chama onDeleted", async () => {
    const onDeleted = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "DELETE") {
        return jsonResponse(null, 204);
      }
      return jsonResponse([]);
    });

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        onDeleted={onDeleted}
        pollMs={0}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Excluir conversa" }));

    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
    expect(window.confirm).toHaveBeenCalledWith(
      "Apagar todo o histórico desta conversa? Essa ação não pode ser desfeita — as mensagens serão excluídas permanentemente.",
    );
  });

  it("não exclui quando o usuário cancela a confirmação", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Excluir conversa" }));

    expect(
      backendFetchMock.mock.calls.some(([, init]) => init?.method === "DELETE"),
    ).toBe(false);
  });

  it("mostra erro quando a exclusão falha", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "DELETE") {
        return jsonResponse({ detail: "erro" }, 500);
      }
      return jsonResponse([]);
    });

    render(
      <ConversationThread
        conversation={conversation("agent")}
        onConversationUpdate={() => {}}
        pollMs={0}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Excluir conversa" }));

    await waitFor(() =>
      expect(
        screen.getByText("Não foi possível excluir a conversa. Tente novamente."),
      ).toBeInTheDocument(),
    );
  });
});
