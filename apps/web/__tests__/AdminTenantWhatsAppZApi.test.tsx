import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminTenantWhatsAppZApi } from "@/components/AdminTenantWhatsAppZApi";
import { adminBackendFetch } from "@/lib/admin-client-api";

vi.mock("@/lib/admin-client-api", () => ({
  adminBackendFetch: vi.fn(),
}));

const mockedFetch = adminBackendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("AdminTenantWhatsAppZApi", () => {
  it("provisiona uma instância e mostra a confirmação", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "platform-admin/tenants/t1/whatsapp" && !init) {
        return { ok: true, json: async () => null };
      }
      if (path === "platform-admin/tenants/t1/whatsapp/zapi") {
        return {
          ok: true,
          json: async () => ({
            provider: "zapi",
            display_phone_number: "Aguardando pareamento",
            status: "disconnected",
            connected_at: "2026-08-12T12:00:00Z",
            managed_by_advoxs: true,
          }),
        };
      }
      return { ok: false, json: async () => null };
    });

    render(<AdminTenantWhatsAppZApi tenantId="t1" />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /provisionar/i })).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByLabelText(/instance id/i), { target: { value: "inst-123" } });
    fireEvent.change(screen.getByLabelText(/^token$/i), { target: { value: "token-abc" } });
    fireEvent.change(screen.getByLabelText(/client-token/i), {
      target: { value: "client-token-abc" },
    });
    fireEvent.click(screen.getByRole("button", { name: /provisionar/i }));

    await waitFor(() =>
      expect(screen.getByText(/o tenant já pode escanear o qr code/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/gerenciada pela advoxs/i)).toBeInTheDocument();
  });

  it("mostra o aviso de pedido pendente e some depois de provisionar", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "platform-admin/tenants/t1/whatsapp" && !init) {
        return { ok: true, json: async () => null };
      }
      if (path === "platform-admin/tenants/t1/whatsapp-request" && !init) {
        return {
          ok: true,
          json: async () => ({ status: "pending", requested_at: "2026-08-12T12:00:00Z" }),
        };
      }
      if (path === "platform-admin/tenants/t1/whatsapp/zapi") {
        return {
          ok: true,
          json: async () => ({
            provider: "zapi",
            display_phone_number: "Aguardando pareamento",
            status: "disconnected",
            connected_at: "2026-08-12T12:00:00Z",
            managed_by_advoxs: true,
          }),
        };
      }
      return { ok: false, json: async () => null };
    });

    render(<AdminTenantWhatsAppZApi tenantId="t1" />);

    await waitFor(() =>
      expect(screen.getByText(/pediu a conexão gerenciada em/i)).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByLabelText(/instance id/i), { target: { value: "inst-123" } });
    fireEvent.change(screen.getByLabelText(/^token$/i), { target: { value: "token-abc" } });
    fireEvent.change(screen.getByLabelText(/client-token/i), {
      target: { value: "client-token-abc" },
    });
    fireEvent.click(screen.getByRole("button", { name: /provisionar/i }));

    await waitFor(() =>
      expect(screen.getByText(/o tenant já pode escanear o qr code/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/pediu a conexão gerenciada em/i)).not.toBeInTheDocument();
  });

  it("mostra a conexão existente ao carregar", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        provider: "zapi",
        display_phone_number: "5511999998888",
        status: "connected",
        connected_at: "2026-08-12T12:00:00Z",
        managed_by_advoxs: false,
      }),
    });

    render(<AdminTenantWhatsAppZApi tenantId="t1" />);

    await waitFor(() => expect(screen.getByText(/conta própria do tenant/i)).toBeInTheDocument());
  });

  it("mostra erro sem quebrar quando a Z-API rejeita as credenciais", async () => {
    mockedFetch.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "platform-admin/tenants/t1/whatsapp" && !init) {
        return { ok: true, json: async () => null };
      }
      return { ok: false, json: async () => ({ detail: "credenciais inválidas" }) };
    });

    render(<AdminTenantWhatsAppZApi tenantId="t1" />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /provisionar/i })).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByLabelText(/instance id/i), { target: { value: "inst-123" } });
    fireEvent.change(screen.getByLabelText(/^token$/i), { target: { value: "token-abc" } });
    fireEvent.change(screen.getByLabelText(/client-token/i), {
      target: { value: "client-token-abc" },
    });
    fireEvent.click(screen.getByRole("button", { name: /provisionar/i }));

    await waitFor(() => expect(screen.getByText(/credenciais inválidas/i)).toBeInTheDocument());
  });
});
