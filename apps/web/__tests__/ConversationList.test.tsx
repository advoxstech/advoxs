import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConversationList } from "@/components/ConversationList";
import type { Conversation } from "@/lib/types";

const conversations: Conversation[] = [
  {
    id: "c1",
    contact_phone_number: "5511999998888",
    state: "agent",
    last_message_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
    summary: null,
    summary_generated_at: null,
  },
  {
    id: "c2",
    contact_phone_number: "5521988887777",
    state: "human",
    last_message_at: null,
    created_at: new Date().toISOString(),
    summary: null,
    summary_generated_at: null,
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
});
