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
    current_agent_name: null,
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
    current_agent_name: null,
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

    expect(screen.getByText("IA disponível")).toBeInTheDocument();
    expect(screen.getByText("Atendimento humano")).toBeInTheDocument();
    expect(screen.getByText("+55 11 99999-8888")).toBeInTheDocument();
  });

  it("mostra o nome do agente quando current_agent_name está presente", () => {
    render(
      <ConversationList
        conversations={[{ ...conversations[0], current_agent_name: "Condominial" }]}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText("Condominial disponível")).toBeInTheDocument();
  });

  it("mostra aguardando pagamento quando a conversa está no billing gate", () => {
    render(
      <ConversationList
        conversations={[
          {
            ...conversations[0],
            state: "billing_gate",
            current_agent_name: "Condominial",
          },
        ]}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText("Aguardando pagamento")).toBeInTheDocument();
    expect(screen.queryByText("Condominial disponível")).not.toBeInTheDocument();
    expect(screen.queryByText("IA disponível")).not.toBeInTheDocument();
  });

  it.each([
    ["processing", "IA processando"],
    ["failed", "Falha no atendimento"],
  ] as const)("mostra o status operacional %s", (status, label) => {
    render(
      <ConversationList
        conversations={[{ ...conversations[0], status }]}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText(label)).toBeInTheDocument();
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

  it("prioriza a prévia da última mensagem e não mostra valores financeiros", () => {
    render(
      <ConversationList
        conversations={[
          {
            ...conversations[0],
            last_message_preview: "Preciso de ajuda com meu contrato",
            end_customer_balance: 128.5,
          },
        ]}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText("Preciso de ajuda com meu contrato")).toBeInTheDocument();
    expect(screen.queryByText("128,5 créditos")).not.toBeInTheDocument();
  });

  it("mostra uma descrição do anexo quando não há texto", () => {
    render(
      <ConversationList
        conversations={[
          {
            ...conversations[0],
            last_message_media_type: "image/jpeg",
            last_message_sender_type: "contact",
          },
        ]}
        loaded
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText("Imagem recebida")).toBeInTheDocument();
  });

  it("não exibe o ciclo financeiro na lista de conversas", () => {
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

    expect(screen.queryByText("20 de 200 créditos usados")).not.toBeInTheDocument();
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
