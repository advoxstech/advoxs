import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ContactsPanel } from "@/components/ContactsPanel";
import { ConversationsPanel } from "@/components/ConversationsPanel";
import { ConversationThread } from "@/components/ConversationThread";
import { backendFetch } from "@/lib/client-api";
import type { Conversation } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({ backendFetch: vi.fn() }));
vi.mock("@/components/TeamSession", () => ({
  useTeamSession: () => ({ role: "admin", id: "admin" }),
}));
const fetchMock = vi.mocked(backendFetch);
const tag = { id: "tag-1", name: "Suporte", color: "#2563EB" };
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
const chat = {
  id: "chat-1",
  contact_phone_number: "557996028480",
  state: "human",
  human_only: true,
  is_test: false,
  tags: [tag],
  contact_name: "Escritório cliente",
  created_at: "2026-10-29T12:00:00Z",
  last_message_at: null,
  summary: null,
  summary_generated_at: null,
} as Conversation;

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (path) =>
    response(path === "contact-tags" ? [tag] : []),
  );
});

it("cadastra número, tags e regra humana sem precisar de conversa", async () => {
  const changed = vi.fn();
  render(<ContactsPanel onChanged={changed} />);
  fireEvent.change(screen.getByLabelText("Nome ou identificação"), {
    target: { value: "Escritório cliente" },
  });
  fireEvent.change(screen.getByLabelText("WhatsApp com código do país"), {
    target: { value: "+55 (79) 9602-8480" },
  });
  fireEvent.click(await screen.findByRole("checkbox", { name: "Suporte" }));
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Atendimento exclusivamente humano" }),
  );
  fetchMock.mockImplementation(async (path, init) => {
    if (path === "contacts" && init?.method === "POST")
      return response({ id: "contact-1" }, 201);
    return response(path === "contact-tags" ? [tag] : []);
  });
  fireEvent.click(screen.getByRole("button", { name: "Salvar contato" }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  const call = fetchMock.mock.calls.find(
    ([path, init]) => path === "contacts" && init?.method === "POST",
  )!;
  expect(JSON.parse(String(call[1]?.body))).toEqual({
    name: "Escritório cliente",
    phone_number: "+55 (79) 9602-8480",
    human_only: true,
    tag_ids: [tag.id],
  });
});

it("mostra conflito de número sem informar sucesso", async () => {
  const changed = vi.fn();
  render(
    <ContactsPanel
      initialConversation={chat}
      onChanged={changed}
    />,
  );
  await screen.findByRole("checkbox", { name: "Suporte" });
  fetchMock.mockResolvedValue(
    response({ detail: "Número já cadastrado." }, 409),
  );
  fireEvent.click(screen.getByRole("button", { name: "Salvar contato" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Número já cadastrado.",
  );
  expect(changed).not.toHaveBeenCalled();
});

it("filtra por tag no servidor e permite voltar para todas", async () => {
  fetchMock.mockImplementation(async (path) => {
    if (path === "contact-tags") return response([tag]);
    if (String(path).startsWith("conversations?"))
      return response(String(path).includes("untagged=true") ? [] : [chat]);
    return response([]);
  });
  render(<ConversationsPanel pollMs={0} />);
  await screen.findByRole("option", { name: "Suporte" });
  fireEvent.change(screen.getByRole("combobox"), { target: { value: tag.id } });
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([p]) => p.includes("tag_id=tag-1"))).toBe(
      true,
    ),
  );
  expect(await screen.findByText("Escritório cliente")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox"), {
    target: { value: "untagged" },
  });
  await waitFor(() =>
    expect(screen.queryByText("Escritório cliente")).not.toBeInTheDocument(),
  );
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "" } });
  expect(await screen.findByText("Escritório cliente")).toBeInTheDocument();
});

it("permite assumir o contato humano mas bloqueia devolver à IA", async () => {
  const updated = vi.fn();
  const { rerender } = render(
    <ConversationThread
      conversation={chat}
      onConversationUpdate={updated}
      pollMs={0}
    />,
  );
  expect(
    screen.getByRole("button", { name: "Assumir atendimento" }),
  ).toBeEnabled();
  rerender(
    <ConversationThread
      conversation={{ ...chat, assigned_user_id: "admin" }}
      onConversationUpdate={updated}
      pollMs={0}
    />,
  );
  expect(
    screen.getByRole("button", { name: "Devolver para IA" }),
  ).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Resposta" })).toBeEnabled();
});
