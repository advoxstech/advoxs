import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PagamentoConfirmadoContent } from "@/components/PagamentoConfirmadoContent";

describe("PagamentoConfirmadoContent", () => {
  it("mostra a mensagem de sucesso quando status=sucesso", () => {
    render(<PagamentoConfirmadoContent status="sucesso" />);

    expect(screen.getByText("Pagamento recebido")).toBeInTheDocument();
    expect(screen.getByText(/sua compra foi confirmada/i)).toBeInTheDocument();
  });

  it("mostra a mensagem de cancelamento quando status=cancelado", () => {
    render(<PagamentoConfirmadoContent status="cancelado" />);

    expect(screen.getByText("Pagamento cancelado")).toBeInTheDocument();
    expect(screen.getByText(/nenhum valor foi cobrado/i)).toBeInTheDocument();
  });

  it("sem status, cai no texto de sucesso (fallback)", () => {
    render(<PagamentoConfirmadoContent />);

    expect(screen.getByText("Pagamento recebido")).toBeInTheDocument();
  });
});
