import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import HomePage from "@/app/page";

const mockedFetch = vi.fn();

beforeEach(() => {
  mockedFetch.mockReset();
  vi.stubGlobal("fetch", mockedFetch);
});

describe("HomePage", () => {
  it("renderiza os planos a partir do fetch de credit-packages", async () => {
    mockedFetch.mockResolvedValue({
      ok: true,
      json: async () => [
        { id: "p1", name: "Starter", price_brl: 100, credits_granted: 1000 },
        { id: "p2", name: "Growth", price_brl: 250, credits_granted: 2750 },
      ],
    });

    render(await HomePage());

    expect(screen.getByText("Starter")).toBeInTheDocument();
    expect(screen.getByText("Growth")).toBeInTheDocument();
  });

  it("renderiza a página mesmo quando o fetch de planos falha", async () => {
    mockedFetch.mockResolvedValue({ ok: false });

    render(await HomePage());

    expect(screen.getByText("Advoxs")).toBeInTheDocument();
    expect(
      screen.getByText("Não foi possível carregar os planos agora. Tente recarregar a página em instantes."),
    ).toBeInTheDocument();
  });
});
