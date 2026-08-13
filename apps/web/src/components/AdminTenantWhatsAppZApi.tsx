"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { adminBackendFetch } from "@/lib/admin-client-api";

type Connection = {
  provider: "meta" | "zapi";
  display_phone_number: string;
  status: "connected" | "disconnected";
  connected_at: string;
  managed_by_advoxs: boolean;
};

type PendingRequest = { status: "pending" | "fulfilled" | "dismissed"; requested_at: string };

type FormState = { instance_id: string; instance_token: string; client_token: string };

const EMPTY_FORM: FormState = { instance_id: "", instance_token: "", client_token: "" };

function extractErrorDetail(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

/** Fluxo manual de Z-API (sem Programa de Parceiro/Integrador — ver
 * CLAUDE.md, "Conexão via Z-API"): um funcionário da Advoxs cria a instância
 * manualmente no painel da própria Z-API e atribui as credenciais aqui em
 * nome do tenant — o tenant nunca vê instance_id/token, só escaneia o QR
 * code de dentro do próprio painel (`/configuracoes/whatsapp`). */
export function AdminTenantWhatsAppZApi({ tenantId }: { tenantId: string }) {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [pendingRequest, setPendingRequest] = useState<PendingRequest | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const response = await adminBackendFetch(`platform-admin/tenants/${tenantId}/whatsapp`);
        if (response.ok) {
          setConnection(await response.json());
        }
        const requestResponse = await adminBackendFetch(
          `platform-admin/tenants/${tenantId}/whatsapp-request`,
        );
        if (requestResponse.ok) {
          setPendingRequest(await requestResponse.json());
        }
      } finally {
        setLoaded(true);
      }
    }
    void load();
  }, [tenantId]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setSubmitting(true);
    try {
      const response = await adminBackendFetch(
        `platform-admin/tenants/${tenantId}/whatsapp/zapi`,
        {
          method: "POST",
          body: JSON.stringify({
            instance_id: form.instance_id,
            instance_token: form.instance_token,
            client_token: form.client_token,
          }),
        },
      );
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(body, "Falha ao provisionar — tente novamente."));
        return;
      }
      setConnection(body);
      setForm(EMPTY_FORM);
      // O backend já fecha o pedido pendente (se houver) na mesma
      // transação do provisionamento — reflete isso aqui sem novo fetch.
      setPendingRequest(null);
      setFeedback("Provisionado — o tenant já pode escanear o QR code no próprio painel.");
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setSubmitting(false);
    }
  }

  if (!loaded) {
    return null;
  }

  return (
    <div>
      <h2 className="font-display text-lg font-semibold text-ink">
        WhatsApp via Z-API (gerenciado pela Advoxs)
      </h2>
      <p className="mt-1 text-sm text-muted">
        Crie a instância manualmente no painel da Z-API e atribua as credenciais aqui — o tenant
        nunca vê instance_id/token, só escaneia o QR code no próprio painel dele.
      </p>

      {connection && (
        <div className="mt-3 rounded-sm border border-line bg-surface px-4 py-3 text-sm">
          <p className="text-ink">
            Conexão atual: {connection.provider === "zapi" ? "Z-API" : "WhatsApp Business oficial"}{" "}
            · {connection.status === "connected" ? "conectado" : "desconectado"}
            {connection.provider === "zapi" && (
              <> · {connection.managed_by_advoxs ? "gerenciada pela Advoxs" : "conta própria do tenant"}</>
            )}
          </p>
          {connection.provider === "meta" && (
            <p className="mt-1 text-xs text-muted">
              Provisionar uma instância Z-API abaixo substitui esta conexão via Meta.
            </p>
          )}
        </div>
      )}

      {pendingRequest?.status === "pending" && (
        <p className="mt-3 rounded-sm border border-brass/40 bg-brass-soft px-4 py-3 text-sm text-ink">
          Este escritório pediu a conexão gerenciada em{" "}
          {new Date(pendingRequest.requested_at).toLocaleDateString("pt-BR")}.
        </p>
      )}

      {feedback && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <form onSubmit={handleSubmit} className="mt-4 flex max-w-md flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm text-ink">
          Instance ID
          <input
            required
            value={form.instance_id}
            onChange={(event) => setForm({ ...form, instance_id: event.target.value })}
            className="rounded border border-line bg-ground px-3 py-2 text-sm text-ink"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-ink">
          Token
          <input
            required
            type="password"
            value={form.instance_token}
            onChange={(event) => setForm({ ...form, instance_token: event.target.value })}
            className="rounded border border-line bg-ground px-3 py-2 text-sm text-ink"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-ink">
          Client-Token
          <input
            required
            type="password"
            value={form.client_token}
            onChange={(event) => setForm({ ...form, client_token: event.target.value })}
            className="rounded border border-line bg-ground px-3 py-2 text-sm text-ink"
          />
        </label>
        <button
          type="submit"
          disabled={submitting}
          className="w-fit rounded border border-line bg-ground px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
        >
          {submitting ? "Provisionando..." : "Provisionar"}
        </button>
      </form>
    </div>
  );
}
