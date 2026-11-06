import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminTenantWhatsAppOmni } from "@/components/AdminTenantWhatsAppOmni";
import { adminBackendFetch } from "@/lib/admin-client-api";

vi.mock("@/lib/admin-client-api", () => ({
  adminBackendFetch: vi.fn(),
}));

const mockedFetch = adminBackendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

function preencher() {
  fireEvent.change(screen.getByLabelText(/id do canal/i), { target: { value: " canal-1 " } });
  fireEvent.change(screen.getByLabelText(/token do canal/i), { target: { value: "tok-1" } });
}

describe("AdminTenantWhatsAppOmni", () => {
  it("cadastra o canal com os campos opcionais vazios como null", async () => {
    const onConnected = vi.fn();
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        provider: "omni",
        display_phone_number: "WhatsApp oficial",
        status: "connected",
        connected_at: "2026-11-05T12:00:00Z",
        managed_by_advoxs: true,
      }),
    });

    render(<AdminTenantWhatsAppOmni tenantId="t1" onConnected={onConnected} />);
    preencher();
    fireEvent.click(screen.getByRole("button", { name: /cadastrar canal/i }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/canal cadastrado/i));
    expect(onConnected).toHaveBeenCalledOnce();
    const [path, init] = mockedFetch.mock.calls[0];
    expect(path).toBe("platform-admin/tenants/t1/whatsapp/omni");
    expect(JSON.parse(init.body)).toEqual({
      channel_id: "canal-1",
      channel_token: "tok-1",
      display_phone_number: null,
      waba_id: null,
    });
    expect(screen.getByLabelText(/id do canal/i)).toHaveValue("");
  });

  it("envia número e WABA ID quando preenchidos", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({}) });

    render(<AdminTenantWhatsAppOmni tenantId="t1" />);
    preencher();
    fireEvent.change(screen.getByLabelText(/número/i), { target: { value: "5511999998888" } });
    fireEvent.change(screen.getByLabelText(/waba id/i), { target: { value: "waba-9" } });
    fireEvent.click(screen.getByRole("button", { name: /cadastrar canal/i }));

    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    const body = JSON.parse(mockedFetch.mock.calls[0][1].body);
    expect(body.display_phone_number).toBe("5511999998888");
    expect(body.waba_id).toBe("waba-9");
  });

  it("mostra o motivo quando o backend recusa", async () => {
    mockedFetch.mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "Integração Omni desabilitada." }),
    });

    render(<AdminTenantWhatsAppOmni tenantId="t1" />);
    preencher();
    fireEvent.click(screen.getByRole("button", { name: /cadastrar canal/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/integração omni desabilitada/i),
    );
  });

  it("mostra erro de conexão sem quebrar", async () => {
    mockedFetch.mockRejectedValue(new Error("rede"));

    render(<AdminTenantWhatsAppOmni tenantId="t1" />);
    preencher();
    fireEvent.click(screen.getByRole("button", { name: /cadastrar canal/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/falha de conexão/i));
  });
});
