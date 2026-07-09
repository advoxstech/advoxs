import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminDashboardPanel } from "@/components/AdminDashboardPanel";
import { adminBackendFetch } from "@/lib/admin-client-api";

vi.mock("@/lib/admin-client-api", () => ({
  adminBackendFetch: vi.fn(),
}));

const mockedFetch = adminBackendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

const DASHBOARD = {
  tenants_total: 12,
  tenants_by_status: { active: 10, suspended: 2 },
  new_tenants_last_30_days: [{ day: "2026-07-01", count: 3 }],
  revenue_brl_last_30_days: 1500.5,
  credits_summary: { sold: 20000, consumed: 8000 },
  messages_processed: 500,
  agent_executions: 120,
  tokens_consumed: 90000,
  low_balance_tenants: [{ id: "t1", name: "Escritório Baixo", credit_balance: 5 }],
  whatsapp_connected: { connected: 8, total: 12 },
  knowledge_base_usage: { total_files: 30, total_size_bytes: 1048576 },
};

describe("AdminDashboardPanel", () => {
  it("renderiza as métricas a partir do dashboard carregado", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => DASHBOARD });

    render(<AdminDashboardPanel />);

    await waitFor(() => expect(screen.getByText("12")).toBeInTheDocument());
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("Escritório Baixo")).toBeInTheDocument();
    expect(screen.getByText("8 / 12")).toBeInTheDocument();
  });

  it("mostra mensagem de erro quando o dashboard falha ao carregar", async () => {
    mockedFetch.mockResolvedValue({ ok: false });

    render(<AdminDashboardPanel />);

    await waitFor(() =>
      expect(screen.getByText("Não foi possível carregar o dashboard.")).toBeInTheDocument(),
    );
  });
});
