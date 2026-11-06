"use client";

import { useState } from "react";
import type { FormEvent } from "react";

import { adminBackendFetch } from "@/lib/admin-client-api";

type FormState = {
  channel_id: string;
  channel_token: string;
  display_phone_number: string;
  waba_id: string;
};

const EMPTY_FORM: FormState = {
  channel_id: "",
  channel_token: "",
  display_phone_number: "",
  waba_id: "",
};

const FIELD = "rounded border border-line bg-ground px-3 py-2 text-sm text-ink";

function extractErrorDetail(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

/** WhatsApp oficial via Z-API Omni: o número é conectado à Meta pelo painel do
 * Omni (workspace e portfólio da Advoxs) e o canal é cadastrado aqui em nome
 * do escritório. O escritório nunca vê o Omni nem as chaves. */
export function AdminTenantWhatsAppOmni({
  tenantId,
  onConnected,
}: {
  tenantId: string;
  onConnected?: () => void;
}) {
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setSubmitting(true);
    try {
      const response = await adminBackendFetch(
        `platform-admin/tenants/${tenantId}/whatsapp/omni`,
        {
          method: "POST",
          body: JSON.stringify({
            channel_id: form.channel_id.trim(),
            channel_token: form.channel_token.trim(),
            display_phone_number: form.display_phone_number.trim() || null,
            waba_id: form.waba_id.trim() || null,
          }),
        },
      );
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback({
          ok: false,
          text: extractErrorDetail(body, "Falha ao cadastrar o canal — tente novamente."),
        });
        return;
      }
      setForm(EMPTY_FORM);
      setFeedback({
        ok: true,
        text: "Canal cadastrado. O escritório já recebe e envia mensagens pelo WhatsApp oficial.",
      });
      onConnected?.();
    } catch {
      setFeedback({ ok: false, text: "Falha de conexão — tente novamente." });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <h2 className="font-display text-lg font-semibold text-ink">
        WhatsApp oficial via Z-API Omni (gerenciado pela Advoxs)
      </h2>
      <p className="mt-1 text-sm text-muted">
        Conecte o número no painel do Omni (workspace da Advoxs) e cadastre o canal aqui. Número e
        WABA ID só são necessários quando a conta do Omni não tem a função ENTERPRISE; o WABA ID é
        usado nos modelos de follow-up.
      </p>

      {feedback && (
        <p
          role={feedback.ok ? "status" : "alert"}
          className={`mt-3 text-sm ${feedback.ok ? "text-accent" : "text-danger"}`}
        >
          {feedback.text}
        </p>
      )}

      <form onSubmit={handleSubmit} className="mt-4 flex max-w-md flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm text-ink">
          ID do canal
          <input
            required
            value={form.channel_id}
            onChange={(event) => setForm({ ...form, channel_id: event.target.value })}
            className={FIELD}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-ink">
          Token do canal
          <input
            required
            type="password"
            value={form.channel_token}
            onChange={(event) => setForm({ ...form, channel_token: event.target.value })}
            className={FIELD}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-ink">
          Número (opcional)
          <input
            inputMode="tel"
            placeholder="5511999999999"
            value={form.display_phone_number}
            onChange={(event) => setForm({ ...form, display_phone_number: event.target.value })}
            className={FIELD}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-ink">
          WABA ID (opcional)
          <input
            value={form.waba_id}
            onChange={(event) => setForm({ ...form, waba_id: event.target.value })}
            className={FIELD}
          />
        </label>
        <button
          type="submit"
          disabled={submitting}
          className="w-fit rounded border border-line bg-ground px-4 py-2 text-action text-ink transition-colors hover:border-accent disabled:opacity-50"
        >
          {submitting ? "Cadastrando..." : "Cadastrar canal"}
        </button>
      </form>
    </div>
  );
}
