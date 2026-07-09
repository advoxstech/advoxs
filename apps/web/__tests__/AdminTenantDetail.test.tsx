import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminTenantDetail } from "@/components/AdminTenantDetail";
import { adminBackendFetch } from "@/lib/admin-client-api";

vi.mock("@/lib/admin-client-api", () => ({
  adminBackendFetch: vi.fn(),
}));

const mockedFetch = adminBackendFetch as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("AdminTenantDetail", () => {
  it("mostra os dados do tenant, transações e arquivos de KB", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "t1",
        name: "Escritório A",
        email_contato: "a@escritorio.com",
        status: "active",
        credit_balance: 500,
        created_at: "2026-07-01T12:00:00Z",
        recent_transactions: [
          {
            id: "tx1",
            type: "purchase",
            amount_credits: 1000,
            description: "Compra do pacote Starter",
            created_at: "2026-07-01T12:00:00Z",
          },
        ],
        knowledge_base_files: [
          { id: "f1", filename: "regimento.pdf", status: "ready", uploaded_at: "2026-07-01T12:00:00Z" },
        ],
      }),
    });

    render(<AdminTenantDetail tenantId="t1" />);

    await waitFor(() => expect(screen.getByText("Escritório A")).toBeInTheDocument());
    expect(screen.getByText("Compra do pacote Starter")).toBeInTheDocument();
    expect(screen.getByText("regimento.pdf")).toBeInTheDocument();
  });

  it("mostra mensagem quando o tenant não é encontrado", async () => {
    mockedFetch.mockResolvedValue({ ok: false, status: 404 });

    render(<AdminTenantDetail tenantId="inexistente" />);

    await waitFor(() =>
      expect(screen.getByText("Escritório não encontrado.")).toBeInTheDocument(),
    );
  });

  it("mostra mensagem de erro genérico quando a resposta não é ok nem 404", async () => {
    mockedFetch.mockResolvedValue({ ok: false, status: 500 });

    render(<AdminTenantDetail tenantId="t1" />);

    await waitFor(() =>
      expect(
        screen.getByText("Não foi possível carregar o escritório."),
      ).toBeInTheDocument(),
    );
  });
});
