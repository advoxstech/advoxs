import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LoginForm } from "@/app/login/LoginForm";

describe("LoginForm", () => {
  it("renderiza os campos e o link de criar conta", () => {
    render(<LoginForm />);

    expect(screen.getByLabelText("E-mail")).toBeInTheDocument();
    expect(screen.getByLabelText("Senha")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Criar conta" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("button", { name: "Entrar no painel" })).toBeInTheDocument();
  });

  it("mostra/oculta a senha ao clicar no botão", () => {
    render(<LoginForm />);

    const passwordInput = screen.getByLabelText("Senha");
    expect(passwordInput).toHaveAttribute("type", "password");

    fireEvent.click(screen.getByRole("button", { name: "Mostrar" }));
    expect(passwordInput).toHaveAttribute("type", "text");

    fireEvent.click(screen.getByRole("button", { name: "Ocultar" }));
    expect(passwordInput).toHaveAttribute("type", "password");
  });

  it("mostra o aviso recebido após uma troca de senha", () => {
    render(<LoginForm notice="Senha alterada. Entre novamente com sua nova senha." />);

    expect(screen.getByRole("status")).toHaveTextContent(
      "Senha alterada. Entre novamente com sua nova senha.",
    );
  });
});
