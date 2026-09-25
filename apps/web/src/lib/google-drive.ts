"use client";

export type DriveConfig = {
  enabled: boolean;
  client_id: string;
  api_key: string;
  project_number: string;
};

type PickerResult = { action: string; docs?: { id: string; name: string }[] };
type Picker = { setVisible: (visible: boolean) => void; dispose: () => void };
type View = { setMimeTypes: (types: string) => View; setIncludeFolders: (value: boolean) => View };
type Builder = {
  setDeveloperKey: (value: string) => Builder;
  setAppId: (value: string) => Builder;
  setOAuthToken: (value: string) => Builder;
  setOrigin: (value: string) => Builder;
  setLocale: (value: string) => Builder;
  addView: (view: View) => Builder;
  enableFeature: (feature: string) => Builder;
  setCallback: (callback: (result: PickerResult) => void) => Builder;
  build: () => Picker;
};
type GoogleSdk = {
  accounts: { oauth2: { initTokenClient: (options: {
    client_id: string;
    scope: string;
    include_granted_scopes: boolean;
    callback: (result: { access_token?: string; scope?: string; error?: string }) => void;
    error_callback: (error: { type: string }) => void;
  }) => { requestAccessToken: (options: { prompt: string }) => void } } };
  picker: {
    DocsView: new () => View;
    PickerBuilder: new () => Builder;
    Feature: { MULTISELECT_ENABLED: string };
    Action: { PICKED: string; CANCEL: string };
  };
};

declare global {
  interface Window {
    google?: GoogleSdk;
    gapi?: { load: (name: string, options: {
      callback: () => void; onerror: () => void; timeout: number; ontimeout: () => void;
    }) => void };
  }
}

const SCOPE = "https://www.googleapis.com/auth/drive.file";
let loading: Promise<void> | undefined;

function script(id: string, src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    document.getElementById(id)?.remove();
    const element = document.createElement("script");
    element.id = id;
    element.src = src;
    element.async = true;
    const timeout = window.setTimeout(() => {
      element.remove();
      reject(new Error("O Google demorou para carregar. Tente novamente."));
    }, 15000);
    element.onload = () => { clearTimeout(timeout); resolve(); };
    element.onerror = () => {
      clearTimeout(timeout);
      element.remove();
      reject(new Error("Não foi possível carregar o Google Drive."));
    };
    document.head.appendChild(element);
  });
}

export function loadDrivePicker(): Promise<void> {
  if (window.google?.picker && window.google?.accounts) return Promise.resolve();
  loading ??= Promise.all([
    script("advoxs-google-identity", "https://accounts.google.com/gsi/client"),
    script("advoxs-google-picker", "https://apis.google.com/js/api.js"),
  ]).then(() => new Promise<void>((resolve, reject) => {
    const fail = () => reject(new Error("Não foi possível abrir o seletor do Google Drive."));
    if (!window.gapi) { fail(); return; }
    window.gapi.load("picker", { callback: resolve, onerror: fail, timeout: 15000, ontimeout: fail });
  })).catch((error) => { loading = undefined; throw error; });
  return loading;
}

// Deve ser chamado diretamente pelo clique, com SDKs carregados, para evitar popup bloqueado.
export function chooseDriveFiles(config: DriveConfig): Promise<{
  token: string;
  files: { id: string; name: string }[];
} | null> {
  return new Promise((resolve, reject) => {
    const google = window.google;
    if (!google?.picker || !google.accounts) {
      reject(new Error("Aguarde o Google Drive carregar e tente novamente."));
      return;
    }
    const client = google.accounts.oauth2.initTokenClient({
      client_id: config.client_id,
      scope: SCOPE,
      include_granted_scopes: false,
      error_callback: (error) => {
        if (error.type === "popup_closed") resolve(null);
        else reject(new Error("Permita a abertura da janela do Google e tente novamente."));
      },
      callback: (result) => {
        if (!result.access_token || result.error || !result.scope?.split(" ").includes(SCOPE)) {
          reject(new Error("O acesso não foi autorizado. Autorize os arquivos para importar."));
          return;
        }
        try {
          const token = result.access_token;
          const view = new google.picker.DocsView().setIncludeFolders(false).setMimeTypes([
          "application/pdf", "text/plain", "application/vnd.google-apps.document",
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ].join(","));
        const picker = new google.picker.PickerBuilder()
          .setDeveloperKey(config.api_key).setAppId(config.project_number)
          .setOAuthToken(token).setOrigin(window.location.origin).setLocale("pt-BR")
          .addView(view).enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
          .setCallback((event) => {
            if (event.action === google.picker.Action.CANCEL) {
              picker.dispose(); resolve(null);
            } else if (event.action === google.picker.Action.PICKED) {
              picker.dispose();
              const files = [...new Map((event.docs ?? []).map((file) => [file.id, file])).values()];
              if (files.length > 20) reject(new Error("Selecione até 20 arquivos por importação."));
              else resolve({ token, files });
            }
          }).build();
          picker.setVisible(true);
        } catch {
          reject(new Error("Não foi possível abrir o seletor do Google Drive. Tente novamente."));
        }
      },
    });
    client.requestAccessToken({ prompt: "select_account" });
  });
}
