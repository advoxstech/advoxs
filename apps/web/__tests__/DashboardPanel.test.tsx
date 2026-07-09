import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardPanel } from "@/components/DashboardPanel";
import { backendFetch } from "@/lib/client-api";
import type { TenantDashboard } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

const DASHBOARD: TenantDashboard = {
  credit_balance: 1500,
  whatsapp: { connected: true, display_phone_number: "551 **** 4321" },
  conversations: { total: 12, waiting_human: 3 },
  usage_last_30_days: { agent_messages: 87, credits_consumed: 240 },
  knowledge_base: { ready: 5, error: 1 },
  recent_conversations: [
    {
      id: "c1",
      contact_phone_number: "5511999990001",
      state: "agent",
      last_message_at: "2026-07-08T12:00:00Z",
    },
    {
      id: "c2",
      contact_phone_number: "5511999990002",
      state: "human",
      last_message_at: null,
    },
  ],
};

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("DashboardPanel", () => {
  it("renderiza as métricas do dashboard", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => DASHBOARD });

    render(<DashboardPanel />);

    await waitFor(() => expect(screen.getByText("1500")).toBeInTheDocument());
    expect(screen.getByText("551 **** 4321")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("87")).toBeInTheDocument();
    expect(screen.getByText("240")).toBeInTheDocument();
  });

  it("renderiza as conversas recentes com estado traduzido", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => DASHBOARD });

    render(<DashboardPanel />);

    await waitFor(() => expect(screen.getByText("5511999990001")).toBeInTheDocument());
    expect(screen.getByText("agente")).toBeInTheDocument();
    expect(screen.getByText("humano")).toBeInTheDocument();
  });

  it("mostra 'Desconectado' quando não há WhatsApp conectado", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        ...DASHBOARD,
        whatsapp: { connected: false, display_phone_number: null },
      }),
    });

    render(<DashboardPanel />);

    await waitFor(() => expect(screen.getByText("Desconectado")).toBeInTheDocument());
  });

  it("mostra mensagem neutra quando não há conversas", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ ...DASHBOARD, recent_conversations: [] }),
    });

    render(<DashboardPanel />);

    await waitFor(() => expect(screen.getByText("Nenhuma conversa ainda.")).toBeInTheDocument());
  });

  it("mostra erro quando o dashboard falha ao carregar", async () => {
    mockedFetch.mockResolvedValue({ ok: false, status: 500 });

    render(<DashboardPanel />);

    await waitFor(() =>
      expect(screen.getByText("Não foi possível carregar o painel.")).toBeInTheDocument(),
    );
  });
});
