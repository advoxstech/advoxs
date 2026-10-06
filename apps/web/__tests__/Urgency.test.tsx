import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationList } from "@/components/ConversationList";
import { ConversationsPanel } from "@/components/ConversationsPanel";
import { TestConversationThread } from "@/components/TestConversationThread";
import { UrgencyBanner } from "@/components/ConversationUrgency";
import { TenantShell } from "@/components/TenantShell";
import { UrgencyKeywordsPanel } from "@/components/UrgencyKeywordsPanel";
import { backendFetch } from "@/lib/client-api";
import type { Conversation } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const backendFetchMock = vi.mocked(backendFetch);

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function conversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: "c1",
    contact_phone_number: "5511999998888",
    state: "agent",
    is_test: false,
    last_message_at: null,
    created_at: new Date().toISOString(),
    summary: null,
    summary_generated_at: null,
    end_customer_billing_exempt: false,
    end_customer_billing_enabled: false,
    current_agent_name: null,
    ...overrides,
  };
}

const URGENT = {
  urgent_since: "2026-10-02T12:00:00Z",
  urgent_reason: "audiência amanhã às 9h",
  urgent_source: "agent" as const,
};

beforeEach(() => {
  backendFetchMock.mockReset();
});

describe("ConversationList — urgência", () => {
  it("mostra o selo Urgente só nas conversas urgentes", () => {
    render(
      <ConversationList
        conversations={[conversation({ id: "u", ...URGENT }), conversation({ id: "n" })]}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.getAllByText("Urgente")).toHaveLength(1);
  });
});

describe("UrgencyBanner / marcar como urgente", () => {
  it("mostra o motivo e marca como resolvida", async () => {
    const resolved = conversation({ urgent_since: null, urgent_reason: null, urgent_source: null });
    backendFetchMock.mockResolvedValue(jsonResponse(resolved));
    const onUpdate = vi.fn();

    render(<UrgencyBanner conversation={conversation(URGENT)} onUpdate={onUpdate} />);

    expect(screen.getByText(/audiência amanhã às 9h/)).toBeInTheDocument();
    expect(screen.getByText(/Sinalizada pela IA/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Marcar como resolvida" }));

    await waitFor(() => expect(onUpdate).toHaveBeenCalledWith(resolved));
    expect(backendFetchMock).toHaveBeenCalledWith("conversations/c1/urgency", {
      method: "PATCH",
      body: JSON.stringify({ urgent: false }),
    });
  });

  it("não renderiza faixa quando a conversa não é urgente", () => {
    const { container } = render(<UrgencyBanner conversation={conversation()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("marca manualmente como urgente", async () => {
    backendFetchMock.mockImplementation(async (path: string) =>
      path.endsWith("/urgency") ? jsonResponse(conversation(URGENT)) : jsonResponse([]),
    );
    const onUpdate = vi.fn();

    render(
      <TestConversationThread
        conversation={conversation({ is_test: true })}
        onDeleted={() => {}}
        onConversationUpdate={onUpdate}
        pollMs={0}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Mais ações" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Marcar como urgente" }));

    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    expect(backendFetchMock).toHaveBeenCalledWith("conversations/c1/urgency", {
      method: "PATCH",
      body: JSON.stringify({ urgent: true }),
    });
  });
});

describe("ConversationsPanel — filtro Urgentes", () => {
  it("busca só as urgentes ao ativar o filtro", async () => {
    backendFetchMock.mockResolvedValue(jsonResponse([]));

    render(<ConversationsPanel pollMs={0} />);
    await waitFor(() =>
      expect(backendFetchMock).toHaveBeenCalledWith(
        "conversations?origin=real&limit=50&offset=0",
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "Urgentes" }));

    await waitFor(() =>
      expect(backendFetchMock).toHaveBeenCalledWith(
        "conversations?origin=real&urgent=true&limit=50&offset=0",
      ),
    );
    expect(screen.getByRole("button", { name: "Urgentes" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});

describe("TenantShell — contador de urgentes", () => {
  it("mostra o contador vindo de urgent-count e o link de configuração", async () => {
    backendFetchMock.mockImplementation(async (path: string) =>
      path === "conversations/urgent-count" ? jsonResponse({ count: 3 }) : jsonResponse({}, 404),
    );

    render(<TenantShell active="inicio">conteúdo</TenantShell>);

    // Rail (desktop) e barra inferior (celular) mostram o mesmo contador.
    const badges = await screen.findAllByLabelText("3 conversa(s) urgente(s)");
    expect(badges[0]).toHaveTextContent("3");
    expect(screen.getByText("Urgência").closest("a")).toHaveAttribute(
      "href",
      "/configuracoes/urgencia",
    );
  });

  it("não mostra contador quando não há urgentes", async () => {
    backendFetchMock.mockImplementation(async (path: string) =>
      path === "conversations/urgent-count" ? jsonResponse({ count: 0 }) : jsonResponse({}, 404),
    );

    render(<TenantShell active="inicio">conteúdo</TenantShell>);

    await waitFor(() =>
      expect(backendFetchMock).toHaveBeenCalledWith("conversations/urgent-count"),
    );
    expect(screen.queryByLabelText(/conversa\(s\) urgente\(s\)/)).toBeNull();
  });
});

describe("UrgencyKeywordsPanel", () => {
  const despejo = { id: "k1", keyword: "despejo", created_at: "2026-10-02T12:00:00Z" };

  it("lista, adiciona e remove palavras-chave", async () => {
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "urgency-keywords" && !init) return jsonResponse([despejo]);
      if (path === "urgency-keywords" && init?.method === "POST") {
        return jsonResponse({ id: "k2", keyword: "liminar", created_at: "x" }, 201);
      }
      if (path === "urgency-keywords/k1") return jsonResponse(null, 204);
      return jsonResponse({}, 404);
    });

    render(<UrgencyKeywordsPanel />);
    expect(await screen.findByText("despejo")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Ex: audiência amanhã"), {
      target: { value: "liminar" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Adicionar" }));
    expect(await screen.findByText("liminar")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remover despejo" }));
    await waitFor(() => expect(screen.queryByText("despejo")).toBeNull());
  });

  it("mostra o erro do servidor ao adicionar duplicada", async () => {
    backendFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return jsonResponse({ detail: "Essa palavra-chave já está na lista." }, 409);
      }
      return jsonResponse([despejo]);
    });

    render(<UrgencyKeywordsPanel />);
    await screen.findByText("despejo");

    fireEvent.change(screen.getByPlaceholderText("Ex: audiência amanhã"), {
      target: { value: "Despejo" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Adicionar" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Essa palavra-chave já está na lista.",
    );
  });
});
