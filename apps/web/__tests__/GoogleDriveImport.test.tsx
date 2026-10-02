import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GoogleDriveImport } from "@/components/GoogleDriveImport";
import { backendFetch } from "@/lib/client-api";
import { chooseDriveFiles, loadDrivePicker } from "@/lib/google-drive";
import type { Agent } from "@/lib/types";

vi.mock("@/lib/client-api", () => ({ backendFetch: vi.fn() }));
vi.mock("@/lib/google-drive", () => ({ chooseDriveFiles: vi.fn(), loadDrivePicker: vi.fn() }));
const mockedFetch = vi.mocked(backendFetch);
const agents = [{ id: "agent-1", name: "Secretária", is_entry_point: true }] as Agent[];
const refreshed = vi.fn().mockResolvedValue(undefined);
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

function routing(action = "import") {
  mockedFetch.mockImplementation(async (path) => {
    if (path.endsWith("config")) return response({ enabled: true, client_id: "client", api_key: "key", project_number: "123" });
    if (path.endsWith("preview")) return response({ file_id: "doc", filename: "Regras.pdf", version: "2", action, existing_file_id: "old" });
    return response({ result: "processing" }, 202);
  });
}

async function select() {
  fireEvent.click(await screen.findByRole("button", { name: "Importar do Google Drive" }));
  fireEvent.click(await screen.findByRole("button", { name: "Selecionar arquivos no Google Drive" }));
  await screen.findByText("Regras.pdf");
}

describe("GoogleDriveImport", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(loadDrivePicker).mockResolvedValue(undefined);
    vi.mocked(chooseDriveFiles).mockResolvedValue({ token: "temporary-token", files: [{ id: "doc", name: "Regras.pdf" }] });
    routing();
  });

  it("fica oculto e não carrega scripts sem configuração", async () => {
    mockedFetch.mockResolvedValue(response({ enabled: false }));
    render(<GoogleDriveImport agents={agents} onImported={refreshed} />);
    await waitFor(() => expect(mockedFetch).toHaveBeenCalled());
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(loadDrivePicker).not.toHaveBeenCalled();
  });

  it("confirma destino e importa usando somente autorização temporária", async () => {
    render(<GoogleDriveImport agents={agents} onImported={refreshed} />);
    await select();
    expect(screen.getByText(/não serão atualizadas automaticamente/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirmar importação" }));
    await screen.findByText(/Importado. Acompanhe/);
    const call = mockedFetch.mock.calls.find(([path]) => path.endsWith("/import"))!;
    expect(JSON.parse(call[1]!.body as string)).toMatchObject({
      file_id: "doc", expected_version: "2", agent_id: "agent-1", replace_file_id: null,
    });
    expect(refreshed).toHaveBeenCalledOnce();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(screen.queryByText("temporary-token")).not.toBeInTheDocument();
  });

  it("exige marcar explicitamente a atualização do documento", async () => {
    routing("update");
    render(<GoogleDriveImport agents={agents} onImported={refreshed} />);
    await select();
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Confirmar importação" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar importação" }));
    await screen.findByText(/Importado. Acompanhe/);
    const call = mockedFetch.mock.calls.find(([path]) => path.endsWith("/import"))!;
    expect(JSON.parse(call[1]!.body as string).replace_file_id).toBe("old");
  });

  it("não permite importar documento inalterado", async () => {
    routing("unchanged");
    render(<GoogleDriveImport agents={agents} onImported={refreshed} />);
    await select();
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Confirmar importação" })).not.toBeInTheDocument();
  });

  it("cancelamento não inicia a importação", async () => {
    vi.mocked(chooseDriveFiles).mockResolvedValue(null);
    render(<GoogleDriveImport agents={agents} onImported={refreshed} />);
    fireEvent.click(await screen.findByRole("button", { name: "Importar do Google Drive" }));
    fireEvent.click(await screen.findByRole("button", { name: "Selecionar arquivos no Google Drive" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Fechar" })).toBeEnabled());
    expect(mockedFetch.mock.calls.some(([path]) => path.endsWith("preview"))).toBe(false);
  });

  it("continua o lote após falha em um dos arquivos", async () => {
    vi.mocked(chooseDriveFiles).mockResolvedValue({ token: "temporary-token", files: [
      { id: "doc", name: "Regras.pdf" }, { id: "doc2", name: "Outro.pdf" },
    ] });
    mockedFetch.mockImplementation(async (path, init) => {
      if (path.endsWith("config")) return response({ enabled: true });
      const body = JSON.parse(init!.body as string);
      if (path.endsWith("preview")) return response({ file_id: body.file_id,
        filename: body.file_id === "doc" ? "Regras.pdf" : "Outro.pdf", action: "import", version: "2" });
      return body.file_id === "doc" ? response({ detail: "Google indisponível" }, 503)
        : response({ result: "processing" }, 202);
    });
    render(<GoogleDriveImport agents={agents} onImported={refreshed} />);
    await select();
    await screen.findByText("Outro.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Confirmar importação" }));
    await screen.findByText("Google indisponível");
    await screen.findByText(/Importado. Acompanhe/);
    expect(mockedFetch.mock.calls.filter(([path]) => path.endsWith("/import"))).toHaveLength(2);
  });

  it("apresenta erro de autorização e permite selecionar novamente", async () => {
    vi.mocked(chooseDriveFiles).mockRejectedValue(new Error("Autorize o Google novamente"));
    render(<GoogleDriveImport agents={agents} onImported={refreshed} />);
    fireEvent.click(await screen.findByRole("button", { name: "Importar do Google Drive" }));
    fireEvent.click(await screen.findByRole("button", { name: "Selecionar arquivos no Google Drive" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Autorize o Google novamente");
    expect(screen.getByRole("button", { name: "Selecionar arquivos no Google Drive" })).toBeEnabled();
  });
});
