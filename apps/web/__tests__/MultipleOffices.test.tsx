import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import OfficesPage from "@/app/escritorios/page";
import InvitePage from "@/app/convite/page";
import { backendFetch, setOfficeContext } from "@/lib/client-api";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json" },
});
beforeEach(() => { sessionStorage.clear(); window.history.replaceState(null, "", "/"); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("mostra os escritórios e o perfil específico de cada vínculo", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ offices: [
    { id: "one", name: "Escritório A", role: "admin" },
    { id: "two", name: "Escritório B", role: "lawyer" },
  ] })));
  render(<OfficesPage />);
  expect(await screen.findByRole("button", { name: /Escritório A Administrador/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Escritório B Advogado/ })).toBeInTheDocument();
});

it("informa ausência de vínculos sem mostrar dados de outros escritórios", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ offices: [] })));
  render(<OfficesPage />);
  expect(await screen.findByText(/não possui acesso ativo/)).toBeInTheDocument();
});

it("conta existente aceita convite sem enviar ou redefinir senha", async () => {
  window.history.replaceState(null, "", "/convite#token=existing-token");
  const request = vi.fn().mockImplementation(async (_url, init) => {
    const body = JSON.parse(init.body);
    return body.action === "inspect"
      ? json({ existing: true, email: "ana@example.com", office_name: "Office B", role: "lawyer" })
      : json({ status: "accepted" });
  });
  vi.stubGlobal("fetch", request);
  render(<InvitePage />);
  await screen.findByText(/Office B/);
  expect(screen.queryByLabelText("Nova senha")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Aceitar convite" }));
  await screen.findByText(/Convite aceito/);
  expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ token: "existing-token" });
  expect(sessionStorage.getItem("advoxs-invitation")).toBeNull();
});

it("mantém o convite para voltar após autenticar com a conta correta", async () => {
  sessionStorage.setItem("advoxs-invitation", "saved-token");
  const request = vi.fn().mockImplementation(async (_url, init) => JSON.parse(init.body).action === "inspect"
    ? json({ existing: true, email: "ana@example.com", office_name: "Office B", role: "lawyer" })
    : json({ detail: "Entre com o e-mail que recebeu o convite." }, 403));
  vi.stubGlobal("fetch", request);
  render(<InvitePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Aceitar convite" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Entre com o e-mail");
  expect(sessionStorage.getItem("advoxs-invitation")).toBe("saved-token");
});

it("envia o contexto da tela nas ações para rejeitar formulários de abas antigas", async () => {
  const request = vi.fn().mockResolvedValue(json({ status: "ok" }));
  vi.stubGlobal("fetch", request);
  setOfficeContext("office-a");
  await backendFetch("team/invitations", { method: "POST", body: "{}" });
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/backend/team/invitations",
    expect.objectContaining({ headers: expect.objectContaining({ "x-office-id": "office-a" }) })));
});
