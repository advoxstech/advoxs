import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HumanRequestBanner } from "@/components/ConversationUrgency";
import type { Conversation } from "@/lib/types";

describe("Pedido humano", () => {
  const conversation = {
    state: "agent",
    human_requested_at: "2026-11-23T12:00:00Z",
    human_request_reason: "Cliente solicitou atendimento humano",
  } as Conversation;

  it("mostra a pendência sem anunciar pausa da IA", () => {
    render(<HumanRequestBanner conversation={conversation} />);
    expect(screen.getByText("Aguardando atendimento humano")).toBeInTheDocument();
    expect(screen.getByText(/IA permanece disponível/)).toBeInTheDocument();
  });

  it("some quando a equipe assume", () => {
    render(<HumanRequestBanner conversation={{ ...conversation, state: "human" }} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
