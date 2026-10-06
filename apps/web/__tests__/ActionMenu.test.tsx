import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ActionMenu } from "@/components/ActionMenu";

function setup() {
  const urgent = vi.fn();
  const remove = vi.fn();
  render(
    <ActionMenu
      items={[
        { label: "Marcar como urgente", onSelect: urgent },
        { label: "Excluir conversa", onSelect: remove, tone: "danger" },
      ]}
    />,
  );
  return { urgent, remove, trigger: screen.getByRole("button", { name: "Mais ações" }) };
}

describe("ActionMenu", () => {
  it("abre o menu, foca o primeiro item e executa a ação escolhida", () => {
    const { urgent, trigger } = setup();
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("menuitem", { name: "Marcar como urgente" })).toHaveFocus();
    fireEvent.click(screen.getByRole("menuitem", { name: "Marcar como urgente" }));
    expect(urgent).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("navega com as setas e fecha com Esc devolvendo o foco", () => {
    const { remove, trigger } = setup();
    fireEvent.click(trigger);
    const menu = screen.getByRole("menu");

    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: "Excluir conversa" })).toHaveFocus();
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: "Marcar como urgente" })).toHaveFocus();
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    expect(screen.getByRole("menuitem", { name: "Excluir conversa" })).toHaveFocus();

    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(remove).not.toHaveBeenCalled();
  });

  it("fecha ao clicar fora", () => {
    const { trigger } = setup();
    fireEvent.click(trigger);

    fireEvent.pointerDown(document.body);

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
