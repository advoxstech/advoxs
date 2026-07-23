import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConversationList } from "@/components/ConversationList";
import type { Conversation } from "@/lib/types";

const conversations: Conversation[] = [
  {
    id: "c1",
    contact_phone_number: "5511999998888",
    state: "agent",
    is_test: false,
    last_message_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
    summary: null,
    summary_generated_at: null,
    end_customer_billing_exempt: false,
    end_customer_billing_enabled: false,
  },
  {
    id: "c2",
    contact_phone_number: "5521988887777",
    state: "human",
    is_test: false,
    last_message_at: null,
    created_at: new Date().toISOString(),
    summary: null,
    summary_generated_at: null,
    end_customer_billing_exempt: false,
    end_customer_billing_enabled: false,
  },
];

describe("ConversationList", () => {
  it("mostra o estado de cada conversa", () => {
    render(
      <ConversationList
        conversations={conversations}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText("agente respondendo")).toBeInTheDocument();
    expect(screen.getByText("atendimento manual")).toBeInTheDocument();
    expect(screen.getByText("+55 11 99999-8888")).toBeInTheDocument();
  });

  it("chama onSelect com o id da conversa clicada", () => {
    const onSelect = vi.fn();
    render(
      <ConversationList
        conversations={conversations}
        loaded
        selectedId={null}
        onSelect={onSelect}
      />,
    );

    fireEvent.click(screen.getByText("+55 21 98888-7777"));

    expect(onSelect).toHaveBeenCalledWith("c2");
  });

  it("mostra estado vazio quando carregou sem conversas", () => {
    render(
      <ConversationList conversations={[]} loaded selectedId={null} onSelect={() => {}} />,
    );

    expect(screen.getByText(/Nenhuma conversa por aqui ainda/)).toBeInTheDocument();
  });

  it("mostra o saldo do cliente final quando presente", () => {
    render(
      <ConversationList
        conversations={[{ ...conversations[0], end_customer_balance: 128.5 }]}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText("128,5 créditos")).toBeInTheDocument();
  });

  it("não mostra saldo quando end_customer_balance é null", () => {
    render(
      <ConversationList
        conversations={[{ ...conversations[0], end_customer_balance: null }]}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.queryByText(/créditos/)).not.toBeInTheDocument();
  });

  it("mostra o ciclo de créditos (comprado/consumido) quando presente", () => {
    render(
      <ConversationList
        conversations={[
          {
            ...conversations[0],
            end_customer_cycle_total: 200,
            end_customer_cycle_consumed: 20,
          },
        ]}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText("20 de 200 créditos usados")).toBeInTheDocument();
  });

  it("não mostra o ciclo quando end_customer_cycle_total é null", () => {
    render(
      <ConversationList
        conversations={[{ ...conversations[0], end_customer_cycle_total: null }]}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.queryByText(/usados/)).not.toBeInTheDocument();
  });
});
