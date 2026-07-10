import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { TenantNav } from "@/components/TenantNav";
import { backendFetch } from "@/lib/client-api";

vi.mock("@/lib/client-api", () => ({
  backendFetch: vi.fn(),
}));

const mockedFetch = backendFetch as ReturnType<typeof vi.fn>;

describe("TenantNav", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
    mockedFetch.mockResolvedValue({ ok: false });
  });

  it("renderiza o item ativo como texto (não link) e os demais como links", () => {
    render(<TenantNav active="conversas" />);

    expect(screen.getByText("Início").closest("a")).toHaveAttribute("href", "/inicio");
    expect(screen.getByText("Conversas").closest("a")).toBeNull();
    expect(screen.getByText("Base").closest("a")).toHaveAttribute(
      "href",
      "/base-de-conhecimento",
    );
    expect(screen.getByText("Config").closest("a")).toHaveAttribute(
      "href",
      "/configuracoes/whatsapp",
    );
    expect(screen.getByText("Créditos").closest("a")).toHaveAttribute("href", "/creditos");
    expect(screen.getByText("Perfil").closest("a")).toHaveAttribute("href", "/perfil");
  });

  it("marca inicio como ativo quando active='inicio'", () => {
    render(<TenantNav active="inicio" />);

    expect(screen.getByText("Início").closest("a")).toBeNull();
    expect(screen.getByText("Conversas").closest("a")).toHaveAttribute("href", "/conversas");
  });

  it("marca creditos como ativo quando active='creditos'", () => {
    render(<TenantNav active="creditos" />);

    expect(screen.getByText("Créditos").closest("a")).toBeNull();
    expect(screen.getByText("Conversas").closest("a")).toHaveAttribute("href", "/conversas");
  });

  it("marca perfil como ativo quando active='perfil'", () => {
    render(<TenantNav active="perfil" />);

    expect(screen.getByText("Perfil").closest("a")).toBeNull();
    expect(screen.getByText("Início").closest("a")).toHaveAttribute("href", "/inicio");
  });

  it("renderiza todos os itens como links quando active=null", () => {
    render(<TenantNav active={null} />);

    expect(screen.getByText("Conversas").closest("a")).not.toBeNull();
    expect(screen.getByText("Base").closest("a")).not.toBeNull();
    expect(screen.getByText("Config").closest("a")).not.toBeNull();
    expect(screen.getByText("Créditos").closest("a")).not.toBeNull();
  });

  it("renderiza o botão Sair", () => {
    render(<TenantNav active="conversas" />);

    expect(screen.getByRole("button", { name: "Sair" })).toBeInTheDocument();
  });

  it("mostra a logo quando o tenant tem uma (has_logo=true)", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ has_logo: true }) });

    render(<TenantNav active="conversas" />);

    await waitFor(() => expect(screen.getByAltText("Logo do escritório")).toBeInTheDocument());
  });

  it("mantém o monograma quando o tenant não tem logo", async () => {
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ has_logo: false }) });

    render(<TenantNav active="conversas" />);

    await waitFor(() => expect(mockedFetch).toHaveBeenCalledWith("profile"));
    expect(screen.queryByAltText("Logo do escritório")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Advoxs")).toBeInTheDocument();
  });

  it("mantém o monograma quando a busca de perfil falha (fail-safe)", async () => {
    mockedFetch.mockRejectedValue(new Error("network error"));

    render(<TenantNav active="conversas" />);

    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    expect(screen.getByLabelText("Advoxs")).toBeInTheDocument();
  });
});
