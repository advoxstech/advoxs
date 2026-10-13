import { StrictMode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TeamSession, type TeamIdentity } from "@/components/TeamSession";
import { AgentLawyers } from "@/components/TeamPanel";
import { ConversationThread } from "@/components/ConversationThread";
import { TenantNav } from "@/components/TenantNav";
import InvitePage from "@/app/convite/page";
import { backendFetch } from "@/lib/client-api";
import type { Conversation } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({ backendFetch: vi.fn() }));
const request = vi.mocked(backendFetch);
const identity: TeamIdentity = {
  id: "l1",
  name: "Ana",
  email: "ana@example.com",
  role: "lawyer",
};
const json = (body: unknown, status = 200) =>
  ({ ok: status < 400, status, json: async () => body }) as Response;

beforeEach(() => {
  vi.restoreAllMocks();
  request.mockReset();
});

describe("Equipe", () => {
  it("advogado vê apenas as áreas de atendimento e sua conta", () => {
    render(
      <TeamSession.Provider value={identity}>
        <TenantNav active="conversas" />
      </TeamSession.Provider>,
    );
    expect(screen.queryByRole("link", { name: /Equipe/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /Financeiro/ })).toBeNull();
    expect(
      screen.getByRole("link", { name: /Meus agentes/ }),
    ).toBeInTheDocument();
  });

  it("permite selecionar dois advogados com a mesma responsabilidade", async () => {
    request.mockImplementation(async (path) =>
      path === "team"
        ? json([
            { ...identity, active: true },
            {
              id: "l2",
              name: "Bruno",
              email: "b@example.com",
              role: "lawyer",
              active: true,
            },
          ])
        : json([]),
    );
    render(<AgentLawyers agentId="a1" />);
    fireEvent.click(await screen.findByLabelText("Ana"));
    fireEvent.click(screen.getByLabelText("Bruno"));
    fireEvent.click(
      screen.getByRole("button", { name: "Salvar responsáveis" }),
    );
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "team/agents/a1/lawyers",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ user_ids: ["l1", "l2"] }),
        }),
      ),
    );
  });

  it("abrir conversa urgente não pausa a IA; apenas assumir faz PATCH", async () => {
    const conversation = {
      id: "c1",
      contact_phone_number: "5511999998888",
      state: "agent",
      is_test: false,
      created_at: new Date().toISOString(),
      last_message_at: null,
      summary: null,
      summary_generated_at: null,
      end_customer_billing_exempt: false,
      end_customer_billing_enabled: false,
      current_agent_name: "Consumer",
      urgent_since: new Date().toISOString(),
      urgent_source: "agent",
      urgent_reason: "Prazo",
    } as Conversation;
    request.mockImplementation(async (path) =>
      json(path.endsWith("/messages") ? [] : conversation),
    );
    render(
      <TeamSession.Provider value={identity}>
        <ConversationThread
          conversation={conversation}
          onConversationUpdate={vi.fn()}
          pollMs={0}
        />
      </TeamSession.Provider>,
    );
    await waitFor(() => expect(request).toHaveBeenCalled());
    expect(
      request.mock.calls.some(([, init]) => init?.method === "PATCH"),
    ).toBe(false);
    fireEvent.click(
      screen.getByRole("button", { name: /Assumir atendimento/ }),
    );
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "conversations/c1",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ state: "human" }),
        }),
      ),
    );
  });

  it("preserva o convite no StrictMode e só aceita após enviar senha", async () => {
    window.history.replaceState(null, "", "/convite#token=secret-token");
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(json({ status: "accepted" }));
    render(
      <StrictMode>
        <InvitePage />
      </StrictMode>,
    );
    expect(fetcher).not.toHaveBeenCalled();
    expect(window.location.hash).toBe("");
    fireEvent.change(screen.getByLabelText("Nova senha"), {
      target: { value: "secure-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Aceitar convite" }));
    await screen.findByText(/Conta criada/);
    expect(fetcher).toHaveBeenCalledWith(
      "/api/convite",
      expect.objectContaining({
        body: JSON.stringify({
          token: "secret-token",
          password: "secure-password",
        }),
      }),
    );
  });
});
