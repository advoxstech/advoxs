import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AdminNav } from "@/components/AdminNav";

describe("AdminNav", () => {
  it("renderiza o item ativo como texto (não link) e os demais como links", () => {
    render(<AdminNav active="dashboard" />);

    expect(screen.getByText("Dashboard").closest("a")).toBeNull();
    expect(screen.getByText("Tenants").closest("a")).toHaveAttribute("href", "/admin/tenants");
    expect(screen.getByText("Playground").closest("a")).toHaveAttribute(
      "href",
      "/admin/playground",
    );
  });

  it("marca playground como ativo quando active='playground'", () => {
    render(<AdminNav active="playground" />);

    expect(screen.getByText("Playground").closest("a")).toBeNull();
    expect(screen.getByText("Dashboard").closest("a")).toHaveAttribute("href", "/admin");
  });

  it("renderiza o botão Sair", () => {
    render(<AdminNav active="tenants" />);

    expect(screen.getByRole("button", { name: "Sair" })).toBeInTheDocument();
  });
});
