import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TenantNav } from "@/components/TenantNav";

describe("TenantNav", () => {
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
});
