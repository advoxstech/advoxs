import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SignupForm } from "@/components/SignupForm";
import type { CreditPackage } from "@/lib/types";

const PACKAGES: CreditPackage[] = [
  { id: "p1", name: "Starter", price_brl: 100, credits_granted: 1000 },
  { id: "p2", name: "Growth", price_brl: 250, credits_granted: 2750 },
];

describe("SignupForm", () => {
  it("seleciona o primeiro pacote por padrão e atualiza o resumo ao trocar de plano", () => {
    render(<SignupForm packages={PACKAGES} />);

    expect(screen.getByText("Plano Starter · 1.000 créditos")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Comprar por R$ 100,00" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: /Growth/ }));

    expect(screen.getByText("Plano Growth · 2.750 créditos")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Comprar por R$ 250,00" })).toBeInTheDocument();
  });

  it("mostra/oculta a senha ao clicar no botão", () => {
    render(<SignupForm packages={PACKAGES} />);

    const passwordInput = screen.getByLabelText("Senha");
    expect(passwordInput).toHaveAttribute("type", "password");

    fireEvent.click(screen.getByRole("button", { name: "Mostrar" }));
    expect(passwordInput).toHaveAttribute("type", "text");

    fireEvent.click(screen.getByRole("button", { name: "Ocultar" }));
    expect(passwordInput).toHaveAttribute("type", "password");
  });

  it("calcula a força da senha conforme o usuário digita", () => {
    render(<SignupForm packages={PACKAGES} />);

    const passwordInput = screen.getByLabelText("Senha");

    fireEvent.change(passwordInput, { target: { value: "abc" } });
    expect(screen.getByText("Fraca")).toBeInTheDocument();

    fireEvent.change(passwordInput, { target: { value: "Abcdefgh1" } });
    expect(screen.getByText("Média")).toBeInTheDocument();

    fireEvent.change(passwordInput, { target: { value: "Abcdefgh123!@#" } });
    expect(screen.getByText("Forte")).toBeInTheDocument();
  });
});
