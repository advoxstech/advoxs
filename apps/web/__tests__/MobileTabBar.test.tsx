import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MobileTabBar } from "@/components/MobileTabBar";

vi.mock("@/app/conversas/actions", () => ({ logout: vi.fn() }));

describe("MobileTabBar", () => {
  it("mostra os 4 destinos principais e marca o ativo", () => {
    render(<MobileTabBar active="conversas" urgentCount={2} />);
    const nav = screen.getByRole("navigation", { name: "Navegação principal" });

    for (const label of ["Início", "Conversas", "Base", "Agentes"]) {
      expect(within(nav).getByText(label).closest("a")).not.toBeNull();
    }
    expect(within(nav).getByText("Conversas").closest("a")).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByLabelText("2 conversa(s) urgente(s)")).toBeInTheDocument();
    expect(screen.queryByText("Perfil")).not.toBeInTheDocument();
  });

  it("'Mais' abre os demais destinos e fecha com Esc", () => {
    render(<MobileTabBar active="perfil" />);
    const more = screen.getByRole("button", { name: "Mais" });
    expect(more).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(more);

    const sheet = screen.getByRole("dialog", { name: "Mais opções" });
    expect(within(sheet).getByText("Perfil").closest("a")).toHaveAttribute("aria-current", "page");
    expect(within(sheet).getByText("Urgência").closest("a")).toHaveAttribute(
      "href",
      "/configuracoes/urgencia",
    );
    expect(within(sheet).getByRole("button", { name: "Sair" })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(more).toHaveFocus();
  });
});
