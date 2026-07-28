import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MonthlyBarChart } from "@/components/MonthlyBarChart";

describe("MonthlyBarChart", () => {
  it("mostra mensagem quando não há dados", () => {
    render(<MonthlyBarChart data={[]} />);

    expect(screen.getByText(/nenhum valor no período/i)).toBeInTheDocument();
  });

  it("renderiza 1 barra por mês", () => {
    const { container } = render(
      <MonthlyBarChart
        data={[
          { month: "2026-06", total_brl: 100 },
          { month: "2026-07", total_brl: 250 },
        ]}
      />,
    );

    expect(container.querySelectorAll("rect[data-bar]")).toHaveLength(2);
  });
});
