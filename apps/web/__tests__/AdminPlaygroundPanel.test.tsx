import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminPlaygroundPanel } from "@/components/AdminPlaygroundPanel";
import { adminBackendFetch } from "@/lib/admin-client-api";

vi.mock("@/lib/admin-client-api", () => ({
  adminBackendFetch: vi.fn(),
}));

const mockedFetch = adminBackendFetch as ReturnType<typeof vi.fn>;

const TENANTS = [
  { id: "t1", name: "Escritório A", status: "active", credit_balance: 100, created_at: "2026-07-01T00:00:00Z", whatsapp_connected: false },
  { id: "t2", name: "Escritório B", status: "active", credit_balance: 50, created_at: "2026-07-01T00:00:00Z", whatsapp_connected: true },
];

beforeEach(() => {
  mockedFetch.mockReset();
});

function mockTenantsThenMessage(messageResponse: unknown) {
  mockedFetch.mockImplementation(async (path: string) => {
    if (path === "platform-admin/tenants") {
      return { ok: true, json: async () => TENANTS };
    }
    return { ok: true, json: async () => messageResponse };
  });
}

describe("AdminPlaygroundPanel", () => {
  it("carrega os tenants e permite escolher um", async () => {
    mockTenantsThenMessage({ responses: [], tokens_used: null, current_agent: null, grouped: false });

    render(<AdminPlaygroundPanel />);

    await waitFor(() => expect(screen.getByText("Escritório A")).toBeInTheDocument());
    expect(screen.getByText("Escritório B")).toBeInTheDocument();
  });

  it("envia mensagem e renderiza a resposta com a tag do agente", async () => {
    mockTenantsThenMessage({
      responses: ["Olá! Sou a secretária, como posso ajudar?"],
      tokens_used: 150,
      current_agent: "agente_condominial",
      grouped: false,
    });

    render(<AdminPlaygroundPanel />);
    await waitFor(() => expect(screen.getByText("Escritório A")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Digite uma mensagem..."), {
      target: { value: "tenho uma dúvida sobre condomínio" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));

    await waitFor(() =>
      expect(screen.getByText("Olá! Sou a secretária, como posso ajudar?")).toBeInTheDocument(),
    );
    expect(screen.getByText("tenho uma dúvida sobre condomínio")).toBeInTheDocument();
    expect(screen.getByText("Condominial")).toBeInTheDocument();
  });

  it("mostra aviso quando a mensagem é agrupada pelo debounce", async () => {
    mockTenantsThenMessage({ responses: [], tokens_used: null, current_agent: null, grouped: true });

    render(<AdminPlaygroundPanel />);
    await waitFor(() => expect(screen.getByText("Escritório A")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Digite uma mensagem..."), {
      target: { value: "oi" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));

    await waitFor(() =>
      expect(screen.getByText(/agrupada à execução em andamento/i)).toBeInTheDocument(),
    );
  });

  it("mostra erro inline quando o agente falha, sem apagar o histórico", async () => {
    mockedFetch.mockImplementation(async (path: string) => {
      if (path === "platform-admin/tenants") {
        return { ok: true, json: async () => TENANTS };
      }
      return { ok: false, status: 502 };
    });

    render(<AdminPlaygroundPanel />);
    await waitFor(() => expect(screen.getByText("Escritório A")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Digite uma mensagem..."), {
      target: { value: "oi" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));

    await waitFor(() =>
      expect(screen.getByText(/não foi possível falar com o agente/i)).toBeInTheDocument(),
    );
    expect(screen.getByText("oi")).toBeInTheDocument();
  });

  it("Nova conversa limpa o histórico e a tag volta pra Secretária", async () => {
    mockTenantsThenMessage({
      responses: ["oi!"],
      tokens_used: 10,
      current_agent: "agente_contratos",
      grouped: false,
    });

    render(<AdminPlaygroundPanel />);
    await waitFor(() => expect(screen.getByText("Escritório A")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Digite uma mensagem..."), {
      target: { value: "oi" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enviar" }));
    await waitFor(() => expect(screen.getByText("Contratos")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Nova conversa" }));

    expect(screen.queryByText("oi!")).not.toBeInTheDocument();
    expect(screen.getByText("Secretária")).toBeInTheDocument();
  });
});
