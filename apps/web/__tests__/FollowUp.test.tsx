import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ContactsPanel } from "@/components/ContactsPanel";
import { ConversationThread } from "@/components/ConversationThread";
import { FollowUpDialog } from "@/components/FollowUpDialog";
import { FollowUpHistory } from "@/components/FollowUpHistory";
import { backendFetch } from "@/lib/client-api";
import type { Conversation, FollowUpTemplate } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({ backendFetch: vi.fn() }));
vi.mock("@/components/TeamSession", () => ({
  useTeamSession: () => ({ role: "admin", id: "admin" }),
}));

const fetchMock = vi.mocked(backendFetch);
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });

const template = (overrides: Partial<FollowUpTemplate> = {}): FollowUpTemplate => ({
  name: "retomada",
  language: "pt_BR",
  status: "APPROVED",
  category: "MARKETING",
  parameter_format: "POSITIONAL",
  header_text: null,
  body_text: "Olá, {{1}}. Aqui é do escritório {{2}}.",
  footer_text: null,
  param_names: ["1", "2"],
  supported: true,
  unsupported_reason: null,
  rejected_reason: null,
  ...overrides,
});

const batch = {
  id: "batch-1",
  created_at: "2026-11-03T12:00:00Z",
  created_by_name: "Ana",
  provider: "meta",
  template_name: "retomada",
  text: null,
  total: 2,
  sent: 1,
  failed: 1,
  skipped: 0,
  pending: 0,
};

function postedBody() {
  const call = fetchMock.mock.calls.find(
    ([path, init]) => path === "follow-ups" && init?.method === "POST",
  )!;
  return JSON.parse(String(call[1]?.body));
}

beforeEach(() => {
  fetchMock.mockReset();
});

describe("FollowUpDialog", () => {
  it("Meta: só modelos aprovados e suportados podem ser escolhidos", async () => {
    fetchMock.mockResolvedValue(
      response({
        provider: "meta",
        templates: [
          template(),
          template({ name: "pausado", status: "PAUSED" }),
          template({
            name: "com_imagem",
            supported: false,
            unsupported_reason: "Cabeçalho com mídia ainda não é suportado.",
          }),
        ],
      }),
    );

    render(
      <FollowUpDialog
        recipients={{ contactIds: ["c1"] }}
        recipientLabel="Maria"
        onClose={vi.fn()}
        onSent={vi.fn()}
      />,
    );

    const options = await screen.findAllByRole("option");
    const byText = (text: string) =>
      options.find((o) => o.textContent?.startsWith(text)) as HTMLOptionElement;
    expect(byText("retomada").disabled).toBe(false);
    expect(byText("pausado").disabled).toBe(true);
    expect(byText("pausado").textContent).toContain("pausado");
    expect(byText("com_imagem").disabled).toBe(true);
  });

  it("Meta: preenche variáveis, mostra prévia e envia", async () => {
    const onSent = vi.fn();
    fetchMock.mockImplementation(async (path, init) => {
      if (path === "follow-ups" && init?.method === "POST")
        return response(batch, 201);
      return response({ provider: "meta", templates: [template()] });
    });

    render(
      <FollowUpDialog
        recipients={{ contactIds: ["c1", "c2"] }}
        recipientLabel="2 contatos"
        sampleName="Maria"
        onClose={vi.fn()}
        onSent={onSent}
      />,
    );

    fireEvent.change(await screen.findByLabelText("Modelo aprovado na Meta"), {
      target: { value: "retomada|pt_BR" },
    });
    const send = screen.getByRole("button", { name: "Enviar follow-up" });
    expect(send).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Texto da variável 2"), {
      target: { value: "Silva Advogados" },
    });
    expect(
      screen.getByText("Olá, Maria. Aqui é do escritório Silva Advogados."),
    ).toBeInTheDocument();

    fireEvent.click(send);
    await waitFor(() => expect(onSent).toHaveBeenCalledWith(batch));
    expect(postedBody()).toEqual({
      contact_ids: ["c1", "c2"],
      conversation_ids: [],
      tag_ids: [],
      template_name: "retomada",
      language: "pt_BR",
      params: [
        { source: "contact_name", value: "cliente" },
        { source: "text", value: "Silva Advogados" },
      ],
    });
  });

  it("Z-API: envia texto livre com o nome do contato", async () => {
    const onSent = vi.fn();
    fetchMock.mockImplementation(async (path, init) => {
      if (path === "follow-ups" && init?.method === "POST")
        return response({ ...batch, provider: "zapi" }, 201);
      return response({ provider: "zapi", templates: [] });
    });

    render(
      <FollowUpDialog
        recipients={{ tagIds: ["tag-1"] }}
        recipientLabel="tag"
        onClose={vi.fn()}
        onSent={onSent}
      />,
    );

    fireEvent.change(await screen.findByLabelText("Mensagem"), {
      target: { value: "Oi {{nome}}, tudo bem?" },
    });
    expect(screen.getByText("Oi cliente, tudo bem?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Enviar follow-up" }));

    await waitFor(() => expect(onSent).toHaveBeenCalled());
    expect(postedBody()).toMatchObject({
      tag_ids: ["tag-1"],
      text: "Oi {{nome}}, tudo bem?",
    });
  });

  it("mostra o motivo quando o envio é recusado", async () => {
    fetchMock.mockImplementation(async (path, init) => {
      if (path === "follow-ups" && init?.method === "POST")
        return response({ detail: "Máximo de 100 destinatários." }, 422);
      return response({ provider: "zapi", templates: [] });
    });

    render(
      <FollowUpDialog
        recipients={{ tagIds: ["tag-1"] }}
        recipientLabel="tag"
        onClose={vi.fn()}
        onSent={vi.fn()}
      />,
    );
    fireEvent.change(await screen.findByLabelText("Mensagem"), {
      target: { value: "Oi" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enviar follow-up" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Máximo de 100 destinatários.",
    );
  });

  it("Esc fecha a janela", async () => {
    fetchMock.mockResolvedValue(response({ provider: "zapi", templates: [] }));
    const onClose = vi.fn();
    render(
      <FollowUpDialog
        recipients={{ contactIds: ["c1"] }}
        recipientLabel="Maria"
        onClose={onClose}
        onSent={vi.fn()}
      />,
    );

    fireEvent.keyDown(window, { key: "Escape" });

    expect(onClose).toHaveBeenCalled();
  });
});

describe("FollowUpHistory", () => {
  it("resume os envios e mostra o motivo da falha por contato", async () => {
    fetchMock.mockImplementation(async (path) => {
      if (path === "follow-ups/batch-1")
        return response({
          ...batch,
          recipients: [
            {
              id: "r1",
              phone: "5579999990000",
              contact_name: "Maria",
              status: "failed",
              error: "O modelo foi pausado pela Meta.",
              sent_at: null,
              conversation_id: null,
            },
          ],
        });
      return response([batch]);
    });

    render(<FollowUpHistory pollMs={0} />);

    expect(
      await screen.findByText(/1 de 2 enviados · 1 com falha/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ver contatos" }));
    expect(
      await screen.findByText(/Falhou — O modelo foi pausado pela Meta\./),
    ).toBeInTheDocument();
  });
});

describe("ContactsPanel", () => {
  it("envia follow-up para os contatos selecionados", async () => {
    const contacts = [
      {
        id: "c1",
        name: "Maria",
        phone_number: "5579999990000",
        human_only: false,
        tags: [],
      },
      {
        id: "c2",
        name: "",
        phone_number: "5579888880000",
        human_only: false,
        tags: [],
      },
    ];
    fetchMock.mockImplementation(async (path, init) => {
      const url = String(path);
      if (url.startsWith("contacts?")) return response(contacts);
      if (url === "follow-ups/templates")
        return response({ provider: "zapi", templates: [] });
      if (url === "follow-ups" && init?.method === "POST")
        return response({ ...batch, provider: "zapi", total: 2 }, 201);
      return response([]);
    });

    render(<ContactsPanel onChanged={vi.fn()} />);

    const button = await screen.findByRole("button", {
      name: "Enviar follow-up (0)",
    });
    expect(button).toBeDisabled();
    await screen.findByRole("checkbox", { name: "Selecionar Maria" });
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Selecionar todos desta página" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Enviar follow-up (2)" }));

    expect(await screen.findByRole("dialog")).toHaveTextContent("2 contatos");
    fireEvent.change(await screen.findByLabelText("Mensagem"), {
      target: { value: "Oi {{nome}}" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enviar follow-up" }));

    expect(
      await screen.findByText(/Follow-up enviado para a fila: 2 contato/),
    ).toBeInTheDocument();
    expect(postedBody().contact_ids).toEqual(["c1", "c2"]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("ConversationThread", () => {
  const oldContactMessage = {
    id: "m1",
    sender_type: "contact" as const,
    content: "Vou pensar e retorno.",
    media_url: null,
    media_type: null,
    delivery_status: null,
    created_at: "2026-10-01T12:00:00Z",
  };
  const humanConversation = {
    id: "chat-1",
    contact_phone_number: "5579999990000",
    contact_name: "Maria",
    state: "human",
    assigned_user_id: "admin",
    is_test: false,
    last_message_at: null,
    created_at: "2026-10-01T12:00:00Z",
    summary: null,
    summary_generated_at: null,
    end_customer_billing_exempt: false,
    end_customer_billing_enabled: false,
    current_agent_name: null,
  } as Conversation;

  it("avisa que a janela de 24h fechou e envia follow-up pela conversa", async () => {
    fetchMock.mockImplementation(async (path, init) => {
      const url = String(path);
      if (url.startsWith("conversations/chat-1/messages"))
        return response([oldContactMessage]);
      if (url === "follow-ups/templates")
        return response({ provider: "zapi", templates: [] });
      if (url === "follow-ups" && init?.method === "POST")
        return response({ ...batch, provider: "zapi", total: 1 }, 201);
      return response({});
    });

    render(
      <ConversationThread
        conversation={humanConversation}
        onConversationUpdate={vi.fn()}
        pollMs={0}
      />,
    );

    expect(
      await screen.findByText(/Passaram mais de 24h desde a última mensagem/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mais ações" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Enviar follow-up" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("Para Maria.");
    fireEvent.change(await screen.findByLabelText("Mensagem"), {
      target: { value: "Oi {{nome}}" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enviar follow-up" }));

    expect(
      await screen.findByText(/Follow-up na fila de envio/),
    ).toBeInTheDocument();
    expect(postedBody().conversation_ids).toEqual(["chat-1"]);
  });
});
