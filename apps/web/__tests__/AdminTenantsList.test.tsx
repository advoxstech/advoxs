import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminTenantsList } from "@/components/AdminTenantsList";
import { adminBackendFetch } from "@/lib/admin-client-api";

vi.mock("@/lib/admin-client-api", () => ({
  adminBackendFetch: vi.fn(),
}));

const mockedFetch = adminBackendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("AdminTenantsList", () => {
  it("lista os tenants com status e WhatsApp", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          id: "t1",
          name: "Escritório A",
          status: "active",
          credit_balance: 500,
          created_at: "2026-07-01T12:00:00Z",
          whatsapp_connected: true,
        },
        {
          id: "t2",
          name: "Escritório B",
          status: "suspended",
          credit_balance: 0,
          created_at: "2026-06-01T12:00:00Z",
          whatsapp_connected: false,
        },
      ],
    });

    render(<AdminTenantsList />);

    await waitFor(() => expect(screen.getByText("Escritório A")).toBeInTheDocument());
    expect(screen.getByText("Escritório B")).toBeInTheDocument();
    expect(screen.getByText("ativo")).toBeInTheDocument();
    expect(screen.getByText("suspenso")).toBeInTheDocument();
  });

  it("mostra mensagem de erro quando a resposta não é ok", async () => {
    mockedFetch.mockResolvedValue({ ok: false, status: 500 });

    render(<AdminTenantsList />);

    await waitFor(() =>
      expect(
        screen.getByText("Não foi possível carregar os escritórios."),
      ).toBeInTheDocument(),
    );
  });
});
