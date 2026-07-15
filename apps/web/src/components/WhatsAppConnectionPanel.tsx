"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { backendFetch } from "@/lib/client-api";

type Connection = {
  display_phone_number: string;
  status: "connected" | "disconnected";
  connected_at: string;
};

type FormState = {
  phone_number_id: string;
  waba_id: string;
  access_token: string;
  pin: string;
};

const EMPTY_FORM: FormState = { phone_number_id: "", waba_id: "", access_token: "", pin: "" };

type WebhookConfig = { callback_url: string; verify_token: string };

function extractErrorDetail(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

const STATUS_LABEL: Record<Connection["status"], string> = {
  connected: "conectado",
  disconnected: "desconectado",
};

const STATUS_CLASS: Record<Connection["status"], string> = {
  connected: "bg-accent-soft text-accent",
  disconnected: "bg-brass-soft text-brass",
};

export function WhatsAppConnectionPanel() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [webhookConfig, setWebhookConfig] = useState<WebhookConfig | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  async function load() {
    try {
      const response = await backendFetch("whatsapp/connection");
      if (response.ok) {
        setConnection(await response.json());
      }
      const configResponse = await backendFetch("whatsapp/webhook-config");
      if (configResponse.ok) {
        const config = await configResponse.json().catch(() => null);
        if (config?.callback_url && config?.verify_token) {
          setWebhookConfig(config);
        }
      }
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setSubmitting(true);
    try {
      const response = await backendFetch("whatsapp/connect", {
        method: "POST",
        body: JSON.stringify(form),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(body, "Falha ao conectar — tente novamente."));
        return;
      }
      setConnection(body);
      setShowForm(false);
      setForm(EMPTY_FORM);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDisconnect() {
    if (!window.confirm("Desconectar o número de WhatsApp deste escritório?")) return;
    setFeedback(null);
    try {
      const response = await backendFetch("whatsapp/disconnect", { method: "POST" });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(body, "Falha ao desconectar — tente novamente."));
        return;
      }
      setConnection(body);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    }
  }

  async function handleCopy(field: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(field);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      // clipboard indisponível (http/permissão) — sem feedback, sem quebrar
    }
  }

  if (!loaded) {
    return (
      <main className="flex flex-1 items-center justify-center bg-ground text-sm text-muted">
        Carregando...
      </main>
    );
  }

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      <header className="border-b border-line px-8 py-5">
        <h1 className="font-display text-xl font-semibold text-ink">WhatsApp Business</h1>
        <p className="text-sm text-muted">
          Conecte o número de WhatsApp Business do escritório para os agentes atenderem pelo
          canal.
        </p>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {connection && !showForm ? (
          <div className="max-w-md rounded border border-line bg-surface p-6">
            <div className="flex items-center justify-between">
              <p className="font-medium text-ink">{connection.display_phone_number}</p>
              <span
                className={`rounded-full px-3 py-1 font-mono text-[10px] uppercase tracking-[0.15em] ${STATUS_CLASS[connection.status]}`}
              >
                {STATUS_LABEL[connection.status]}
              </span>
            </div>
            <p className="mt-1 text-xs text-muted">
              Vinculado em {new Date(connection.connected_at).toLocaleDateString("pt-BR")}
            </p>
            <div className="mt-4 flex gap-4">
              {connection.status === "connected" && (
                <button
                  type="button"
                  onClick={() => void handleDisconnect()}
                  className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
                >
                  Desconectar
                </button>
              )}
              <button
                type="button"
                onClick={() => setShowForm(true)}
                className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
              >
                {connection.status === "connected" ? "Trocar número" : "Reconectar"}
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="flex max-w-md flex-col gap-4">
            <label className="flex flex-col gap-1 text-sm text-ink">
              Phone Number ID
              <input
                required
                value={form.phone_number_id}
                onChange={(event) => setForm({ ...form, phone_number_id: event.target.value })}
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-ink">
              WhatsApp Business Account ID
              <input
                required
                value={form.waba_id}
                onChange={(event) => setForm({ ...form, waba_id: event.target.value })}
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-ink">
              Access Token
              <input
                required
                type="password"
                value={form.access_token}
                onChange={(event) => setForm({ ...form, access_token: event.target.value })}
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-ink">
              PIN (6 dígitos)
              <input
                required
                type="password"
                inputMode="numeric"
                maxLength={6}
                value={form.pin}
                onChange={(event) => setForm({ ...form, pin: event.target.value })}
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <div className="flex gap-4">
              <button
                type="submit"
                disabled={submitting}
                className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
              >
                {submitting ? "Conectando..." : "Conectar"}
              </button>
              {connection && (
                <button
                  type="button"
                  onClick={() => {
                    setShowForm(false);
                    setForm(EMPTY_FORM);
                  }}
                  className="font-mono text-xs uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                >
                  Cancelar
                </button>
              )}
            </div>
          </form>
        )}

        {webhookConfig && (
          <section className="mt-8 max-w-xl rounded border border-line bg-surface p-6">
            <h2 className="font-display text-base font-semibold text-ink">
              Configurar webhook na Meta
            </h2>
            <p className="mt-1 text-sm text-muted">
              Passo obrigatório: sem o webhook, as mensagens enviadas ao número não chegam à
              plataforma.
            </p>
            <ol className="mt-4 flex list-decimal flex-col gap-3 pl-5 text-sm text-ink">
              <li>
                No painel do seu app em developers.facebook.com, abra{" "}
                <span className="font-medium">WhatsApp → Configuration → Webhook</span> e clique
                em Edit.
              </li>
              <li>
                Preencha com os valores abaixo e clique em Verify and save:
                <div className="mt-2 flex flex-col gap-2">
                  <div className="flex items-center gap-2">
                    <input
                      readOnly
                      aria-label="Callback URL"
                      value={webhookConfig.callback_url}
                      className="flex-1 rounded border border-line bg-ground px-3 py-2 font-mono text-xs text-ink"
                    />
                    <button
                      type="button"
                      aria-label="Copiar Callback URL"
                      onClick={() => void handleCopy("url", webhookConfig.callback_url)}
                      className="rounded border border-line px-3 py-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                    >
                      {copied === "url" ? "Copiado!" : "Copiar"}
                    </button>
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      readOnly
                      aria-label="Verify token"
                      value={webhookConfig.verify_token}
                      className="flex-1 rounded border border-line bg-ground px-3 py-2 font-mono text-xs text-ink"
                    />
                    <button
                      type="button"
                      aria-label="Copiar Verify token"
                      onClick={() => void handleCopy("token", webhookConfig.verify_token)}
                      className="rounded border border-line px-3 py-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                    >
                      {copied === "token" ? "Copiado!" : "Copiar"}
                    </button>
                  </div>
                </div>
              </li>
              <li>
                Ainda em Webhook, na lista{" "}
                <span className="font-medium">Webhook fields</span>, clique em Manage e assine o
                campo <code className="rounded bg-ground px-1">messages</code>.
              </li>
            </ol>
          </section>
        )}
      </div>
    </main>
  );
}
