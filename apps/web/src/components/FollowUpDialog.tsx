"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { backendFetch } from "@/lib/client-api";
import type {
  FollowUpBatch,
  FollowUpTemplate,
  FollowUpTemplates,
} from "@/lib/types";

export interface FollowUpRecipients {
  contactIds?: string[];
  conversationIds?: string[];
  tagIds?: string[];
}

type ParamSource = "text" | "contact_name";
interface ParamInput {
  source: ParamSource;
  value: string;
}

const field = "rounded-sm border border-line bg-ground px-3 py-2 text-sm";
const primaryButton =
  "rounded border border-accent bg-accent px-4 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-50";
const secondaryButton =
  "rounded border border-line px-3 py-2 text-action text-muted transition-colors hover:text-ink disabled:opacity-50";
const VARIABLE = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;
const DEFAULT_NAME = "cliente";

const STATUS_LABEL: Record<string, string> = {
  APPROVED: "aprovado",
  PENDING: "em análise",
  IN_REVIEW: "em análise",
  REJECTED: "rejeitado",
  PAUSED: "pausado",
  DISABLED: "desativado",
};

function templateKey(template: FollowUpTemplate) {
  return `${template.name}|${template.language}`;
}

function usable(template: FollowUpTemplate) {
  return template.status === "APPROVED" && template.supported;
}

function errorDetail(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" ? detail : fallback;
}

/** Janela de envio de follow-up: modelo aprovado (Meta) ou texto (Z-API). */
export function FollowUpDialog({
  recipients,
  recipientLabel,
  sampleName,
  onClose,
  onSent,
}: {
  recipients: FollowUpRecipients;
  recipientLabel: string;
  sampleName?: string;
  onClose: () => void;
  onSent: (batch: FollowUpBatch) => void;
}) {
  const [data, setData] = useState<FollowUpTemplates | null>(null);
  const [loadError, setLoadError] = useState("");
  const [selectedKey, setSelectedKey] = useState("");
  const [params, setParams] = useState<ParamInput[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    void backendFetch("follow-ups/templates")
      .then(async (response) => {
        const body = await response.json().catch(() => null);
        if (!response.ok)
          throw new Error(
            errorDetail(body, "Não foi possível carregar os modelos."),
          );
        if (active) setData(body as FollowUpTemplates);
      })
      .catch((err: Error) => {
        if (active) setLoadError(err.message);
      });
    return () => {
      active = false;
    };
  }, []);

  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    panelRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCloseRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const template = data?.templates.find((t) => templateKey(t) === selectedKey);

  const chooseTemplate = (key: string) => {
    setSelectedKey(key);
    const next = data?.templates.find((t) => templateKey(t) === key);
    setParams(
      (next?.param_names ?? []).map((_, index) => ({
        source: index === 0 ? "contact_name" : "text",
        value: index === 0 ? DEFAULT_NAME : "",
      })),
    );
  };

  const preview = useMemo(() => {
    const name = sampleName?.trim();
    if (data?.provider === "zapi") {
      return text.replaceAll("{{nome}}", name || DEFAULT_NAME);
    }
    if (!template) return "";
    const values = new Map(
      template.param_names.map((paramName, index) => {
        const param = params[index];
        const value =
          param?.source === "contact_name"
            ? name || param.value || DEFAULT_NAME
            : param?.value || `{{${paramName}}}`;
        return [paramName, value];
      }),
    );
    const body = template.body_text.replace(
      VARIABLE,
      (match, paramName: string) => values.get(paramName) ?? match,
    );
    return [template.header_text, body, template.footer_text]
      .filter(Boolean)
      .join("\n\n");
  }, [data?.provider, params, sampleName, template, text]);

  const ready =
    data?.provider === "zapi"
      ? text.trim().length > 0
      : !!template &&
        params.every((p) => p.source === "contact_name" || p.value.trim());

  const send = async () => {
    setSending(true);
    setError("");
    try {
      const response = await backendFetch("follow-ups", {
        method: "POST",
        body: JSON.stringify({
          contact_ids: recipients.contactIds ?? [],
          conversation_ids: recipients.conversationIds ?? [],
          tag_ids: recipients.tagIds ?? [],
          ...(data?.provider === "zapi"
            ? { text }
            : {
                template_name: template?.name,
                language: template?.language,
                params,
              }),
        }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setError(errorDetail(body, "Não foi possível enviar o follow-up."));
        return;
      }
      onSent(body as FollowUpBatch);
    } catch {
      setError("Falha de conexão. Confira o histórico antes de tentar de novo.");
    } finally {
      setSending(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="follow-up-title"
        className="max-h-[90dvh] w-full max-w-lg space-y-4 overflow-y-auto rounded border border-line bg-surface p-5 outline-none"
      >
        <div>
          <h2
            id="follow-up-title"
            className="font-display text-xl font-semibold text-ink"
          >
            Enviar follow-up
          </h2>
          <p className="mt-1 text-sm text-muted">Para {recipientLabel}.</p>
        </div>

        {loadError && (
          <p role="alert" className="text-sm text-danger">
            {loadError}
          </p>
        )}
        {!data && !loadError && (
          <p role="status" className="text-sm text-muted">
            Carregando…
          </p>
        )}

        {data && data.provider !== "zapi" && (
          <>
            <label className="block text-sm">
              Modelo aprovado na Meta
              <select
                className={`${field} mt-1 block w-full`}
                value={selectedKey}
                onChange={(event) => chooseTemplate(event.target.value)}
              >
                <option value="">Escolha um modelo</option>
                {data.templates.map((t) => (
                  <option
                    key={templateKey(t)}
                    value={templateKey(t)}
                    disabled={!usable(t)}
                  >
                    {t.name} ({t.language})
                    {t.status !== "APPROVED"
                      ? ` — ${STATUS_LABEL[t.status] ?? t.status.toLowerCase()}`
                      : !t.supported
                        ? " — formato não suportado"
                        : ""}
                  </option>
                ))}
              </select>
            </label>
            {!data.templates.some(usable) && (
              <p className="text-sm text-muted">
                Nenhum modelo pronto para uso. Crie e aprove um modelo em
                Modelos de mensagem, no Gerenciador do WhatsApp.
              </p>
            )}
            {template &&
              template.param_names.map((paramName, index) => (
                <fieldset
                  key={paramName}
                  className="space-y-2 rounded border border-line p-3"
                >
                  <legend className="px-1 text-sm">
                    Variável {`{{${paramName}}}`}
                  </legend>
                  <select
                    aria-label={`Origem da variável ${paramName}`}
                    className={`${field} block w-full`}
                    value={params[index]?.source ?? "text"}
                    onChange={(event) =>
                      setParams((current) =>
                        current.map((p, i) =>
                          i === index
                            ? {
                                source: event.target.value as ParamSource,
                                value:
                                  event.target.value === "contact_name"
                                    ? DEFAULT_NAME
                                    : "",
                              }
                            : p,
                        ),
                      )
                    }
                  >
                    <option value="contact_name">Nome do contato</option>
                    <option value="text">Texto fixo</option>
                  </select>
                  <input
                    aria-label={
                      params[index]?.source === "contact_name"
                        ? `Nome usado quando o contato não tem nome (${paramName})`
                        : `Texto da variável ${paramName}`
                    }
                    maxLength={500}
                    className={`${field} block w-full`}
                    value={params[index]?.value ?? ""}
                    placeholder={
                      params[index]?.source === "contact_name"
                        ? "Quando não houver nome cadastrado"
                        : "Texto que entra no lugar da variável"
                    }
                    onChange={(event) =>
                      setParams((current) =>
                        current.map((p, i) =>
                          i === index ? { ...p, value: event.target.value } : p,
                        ),
                      )
                    }
                  />
                </fieldset>
              ))}
          </>
        )}

        {data?.provider === "zapi" && (
          <div>
            <label className="block text-sm">
              Mensagem
              <textarea
                rows={5}
                maxLength={4096}
                aria-describedby="follow-up-text-hint"
                className={`${field} mt-1 block w-full`}
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder="Olá, {{nome}}! Podemos continuar seu atendimento?"
              />
            </label>
            <p id="follow-up-text-hint" className="mt-1 text-xs text-muted">
              {"Use {{nome}} para o nome do contato."}
            </p>
          </div>
        )}

        {preview && (
          <div>
            <p className="font-mono text-micro uppercase tracking-[0.12em] text-muted">
              Prévia
            </p>
            <p className="mt-1 whitespace-pre-wrap rounded bg-brass-soft px-3.5 py-2.5 text-sm">
              {preview}
            </p>
          </div>
        )}

        {data && (
          <p className="text-xs text-muted">
            {data.provider === "meta"
              ? "Cada modelo enviado é cobrado pela Meta na conta do escritório. "
              : data.provider === "omni"
                ? "Cada modelo enviado é cobrado pela Meta. "
                : "Mensagens não solicitadas podem levar ao bloqueio do número. "}
            Envie só para quem aceitou receber mensagens do escritório.
          </p>
        )}

        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}

        <div className="flex flex-wrap items-center justify-end gap-3">
          <button type="button" className={secondaryButton} onClick={onClose}>
            Cancelar
          </button>
          <button
            type="button"
            className={primaryButton}
            disabled={!ready || sending}
            onClick={() => void send()}
          >
            {sending ? "Enviando…" : "Enviar follow-up"}
          </button>
        </div>
      </div>
    </div>
  );
}
