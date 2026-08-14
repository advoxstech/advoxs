"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { backendFetch } from "@/lib/client-api";

type Provider = "meta" | "zapi";

type Connection = {
  provider: Provider;
  display_phone_number: string;
  status: "connected" | "disconnected";
  connected_at: string;
  // True quando a instância Z-API foi provisionada manualmente pela Advoxs
  // (painel de admin) em vez do próprio tenant ter colado as credenciais —
  // nesse caso o tenant nunca vê o formulário, só o QR code.
  managed_by_advoxs: boolean;
};

type FormState = {
  phone_number_id: string;
  waba_id: string;
  access_token: string;
  pin: string;
};

const EMPTY_FORM: FormState = { phone_number_id: "", waba_id: "", access_token: "", pin: "" };

type ZApiFormState = { instance_id: string; instance_token: string; client_token: string };

const EMPTY_ZAPI_FORM: ZApiFormState = { instance_id: "", instance_token: "", client_token: "" };

type WebhookConfig = { callback_url: string; verify_token: string };

type ManagedZApiRequest = { status: "pending" | "fulfilled" | "dismissed"; requested_at: string };

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

const PROVIDER_LABEL: Record<Provider, string> = {
  meta: "WhatsApp Business oficial",
  zapi: "Z-API",
};

export function WhatsAppConnectionPanel() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [providerChoice, setProviderChoice] = useState<Provider | null>(null);
  // "form" = tenant vai colar instance_id/token da própria conta Z-API;
  // null = ainda não escolheu entre isso e pedir pra Advoxs configurar.
  const [zapiEntryChoice, setZapiEntryChoice] = useState<"form" | null>(null);
  const [pendingRequest, setPendingRequest] = useState<ManagedZApiRequest | null>(null);
  const [requestingManaged, setRequestingManaged] = useState(false);
  const [cancellingRequest, setCancellingRequest] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [zapiForm, setZapiForm] = useState<ZApiFormState>(EMPTY_ZAPI_FORM);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [webhookConfig, setWebhookConfig] = useState<WebhookConfig | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [qrcode, setQrcode] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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
      const requestResponse = await backendFetch("whatsapp/managed-zapi-request");
      if (requestResponse.ok) {
        setPendingRequest(await requestResponse.json());
      }
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void load();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // Instância provisionada pela Advoxs (painel de admin, ver "Conexão via
  // Z-API" / fluxo manual no CLAUDE.md) e ainda não pareada: o tenant nunca
  // vê instance_id/token, então pula o formulário e o seletor de provedor
  // inteiros — vai direto pro QR code.
  const managedPairingPending =
    connection?.provider === "zapi" &&
    connection.managed_by_advoxs &&
    connection.status === "disconnected";

  useEffect(() => {
    if (managedPairingPending && !qrcode) {
      void fetchAndShowQrCode();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [managedPairingPending, qrcode]);

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  function startZApiPolling() {
    stopPolling();
    pollRef.current = setInterval(async () => {
      const response = await backendFetch("whatsapp/zapi-status");
      if (!response.ok) return;
      const body = await response.json().catch(() => null);
      if (body?.status === "connected") {
        setConnection(body);
        setQrcode(null);
        setShowForm(false);
        setProviderChoice(null);
        stopPolling();
      }
    }, 3000);
  }

  function backToPicker() {
    setProviderChoice(null);
    setZapiEntryChoice(null);
    setQrcode(null);
    stopPolling();
    setFeedback(null);
  }

  function startReconnect() {
    setShowForm(true);
    backToPicker();
  }

  async function fetchAndShowQrCode() {
    const qrResponse = await backendFetch("whatsapp/zapi-qrcode");
    if (qrResponse.ok) {
      const qrBody = await qrResponse.json().catch(() => null);
      if (qrBody?.qrcode_base64) {
        setQrcode(qrBody.qrcode_base64);
        startZApiPolling();
        return;
      }
    }
    setFeedback("Falha ao gerar o QR code — tente novamente.");
  }

  async function handleRequestManagedZapi() {
    setFeedback(null);
    setRequestingManaged(true);
    try {
      const response = await backendFetch("whatsapp/request-managed-zapi", { method: "POST" });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(body, "Falha ao enviar a solicitação — tente novamente."));
        return;
      }
      setPendingRequest(body);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setRequestingManaged(false);
    }
  }

  async function handleCancelManagedRequest() {
    setFeedback(null);
    setCancellingRequest(true);
    try {
      const response = await backendFetch("whatsapp/managed-zapi-request/cancel", {
        method: "POST",
      });
      if (!response.ok) {
        setFeedback("Falha ao cancelar a solicitação — tente novamente.");
        return;
      }
      setPendingRequest(null);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setCancellingRequest(false);
    }
  }

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
      setProviderChoice(null);
      setForm(EMPTY_FORM);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleZApiSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setSubmitting(true);
    try {
      // Precisa ficar explícito aqui (não só herdado do fluxo "sem conexão")
      // porque, assim que connect-zapi tiver sucesso, `connection` deixa de
      // ser null — sem showForm=true, `inConnectFlow` cairia pra false e a
      // tela pularia direto pro card de resumo, escondendo o QR code.
      setShowForm(true);
      const response = await backendFetch("whatsapp/connect-zapi", {
        method: "POST",
        body: JSON.stringify({
          instance_id: zapiForm.instance_id,
          instance_token: zapiForm.instance_token,
          client_token: zapiForm.client_token,
        }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(body, "Falha ao conectar — tente novamente."));
        return;
      }
      // Continua em showForm=true / providerChoice="zapi" — a próxima tela
      // (QR code) ainda faz parte do fluxo de conexão, não da conexão pronta.
      setConnection(body);
      setZapiForm(EMPTY_ZAPI_FORM);

      // A instância já pode ter sido pareada fora do nosso fluxo (ex: testada
      // direto no painel da Z-API antes de conectar aqui) — nesse caso o
      // backend já devolve status="connected" e não há QR code pra buscar
      // (a Z-API se recusa a gerar um pra instância já conectada). Pular
      // direto pro card de resumo evita um "Falha ao gerar o QR code" falso.
      if (body?.status === "connected") {
        setShowForm(false);
        return;
      }

      await fetchAndShowQrCode();
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
      setQrcode(null);
      stopPolling();
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

  const inConnectFlow = !connection || showForm;
  // Já pediu pra Advoxs configurar e ainda não tem nenhuma conexão — evita
  // reapresentar o seletor de provedor enquanto o pedido está em aberto.
  const pendingManagedRequest = !connection && pendingRequest?.status === "pending";
  const activeProvider = providerChoice ?? connection?.provider ?? null;
  const isManagedZapi = connection?.provider === "zapi" && connection.managed_by_advoxs;
  const showMetaInstructions = webhookConfig && activeProvider !== "zapi";
  // Instância provisionada pela Advoxs: não há nenhum passo manual pro
  // tenant (nem credenciais, nem webhook) — as instruções de self-service
  // não se aplicam.
  const showZApiInstructions = activeProvider === "zapi" && !isManagedZapi;

  return (
    <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-ground">
      <header className="border-b border-line px-8 py-5">
        <h1 className="font-display text-xl font-semibold text-ink">WhatsApp Business</h1>
        <p className="text-sm text-muted">
          Conecte o número de WhatsApp do escritório para os agentes atenderem pelo canal — pela
          via oficial da Meta ou pela Z-API.
        </p>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {managedPairingPending ? (
          <div className="flex max-w-md flex-col gap-4">
            <p className="text-sm text-ink">
              A Advoxs já configurou seu número — abra o WhatsApp que vai atender, vá em{" "}
              <span className="font-medium">Aparelhos conectados</span> e escaneie o QR code
              abaixo.
            </p>
            {qrcode ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={qrcode} alt="QR code de pareamento da Z-API" className="max-w-xs" />
            ) : (
              <p className="text-xs text-muted">Gerando QR code...</p>
            )}
            <p className="text-xs text-muted">Aguardando pareamento...</p>
          </div>
        ) : !inConnectFlow && connection ? (
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
              Conectado via {PROVIDER_LABEL[connection.provider]} · Vinculado em{" "}
              {new Date(connection.connected_at).toLocaleDateString("pt-BR")}
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
              {!isManagedZapi && (
                <button
                  type="button"
                  onClick={startReconnect}
                  className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                >
                  {connection.status === "connected" ? "Trocar número" : "Reconectar"}
                </button>
              )}
            </div>
          </div>
        ) : pendingManagedRequest ? (
          <div className="flex max-w-md flex-col gap-3">
            <p className="text-sm text-ink">
              Solicitação enviada em{" "}
              {new Date(pendingRequest!.requested_at).toLocaleDateString("pt-BR")} — a Advoxs está
              configurando sua conexão. Você vai ver o QR code aqui automaticamente quando
              estiver pronto.
            </p>
            <button
              type="button"
              onClick={() => void handleCancelManagedRequest()}
              disabled={cancellingRequest}
              className="w-fit font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger disabled:opacity-50"
            >
              {cancellingRequest ? "Cancelando..." : "Cancelar solicitação"}
            </button>
          </div>
        ) : providerChoice === null ? (
          <div className="flex max-w-md flex-col gap-3">
            <p className="text-sm text-ink">Como você quer conectar o WhatsApp?</p>
            <button
              type="button"
              onClick={() => setProviderChoice("meta")}
              className="rounded border border-line bg-surface px-4 py-3 text-left text-sm text-ink transition-colors hover:border-accent"
            >
              WhatsApp Business oficial
              <span className="mt-0.5 block text-xs text-muted">
                Via oficial da Meta — exige aprovação de negócio, mais burocrático.
              </span>
            </button>
            <button
              type="button"
              onClick={() => setProviderChoice("zapi")}
              className="rounded border border-line bg-surface px-4 py-3 text-left text-sm text-ink transition-colors hover:border-accent"
            >
              Z-API
              <span className="mt-0.5 block text-xs text-muted">
                Conexão por QR code, sem aprovação de negócio — mais simples de configurar.
              </span>
            </button>
            {connection && (
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="mt-1 w-fit font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
              >
                Cancelar
              </button>
            )}
          </div>
        ) : providerChoice === "meta" ? (
          <form onSubmit={handleSubmit} className="flex max-w-md flex-col gap-4">
            <button
              type="button"
              onClick={backToPicker}
              className="w-fit font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
            >
              ← Escolher outro provedor
            </button>
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
        ) : providerChoice === "zapi" && zapiEntryChoice === null ? (
          <div className="flex max-w-md flex-col gap-3">
            <button
              type="button"
              onClick={backToPicker}
              className="w-fit font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
            >
              ← Escolher outro provedor
            </button>
            <p className="text-sm text-ink">Como você prefere configurar a Z-API?</p>
            <button
              type="button"
              onClick={() => setZapiEntryChoice("form")}
              className="rounded border border-line bg-surface px-4 py-3 text-left text-sm text-ink transition-colors hover:border-accent"
            >
              Já tenho instance_id e token
              <span className="mt-0.5 block text-xs text-muted">
                Você conecta com a própria conta Z-API do escritório.
              </span>
            </button>
            <button
              type="button"
              onClick={() => void handleRequestManagedZapi()}
              disabled={requestingManaged}
              className="rounded border border-line bg-surface px-4 py-3 text-left text-sm text-ink transition-colors hover:border-accent disabled:opacity-50"
            >
              Não tenho — quero que a Advoxs configure pra mim
              <span className="mt-0.5 block text-xs text-muted">
                A Advoxs cria a instância e avisa você aqui quando estiver pronta pra escanear o
                QR code.
              </span>
            </button>
          </div>
        ) : qrcode ? (
          <div className="flex max-w-md flex-col gap-4">
            <p className="text-sm text-ink">
              Abra o WhatsApp do número que vai atender, vá em Aparelhos conectados e escaneie o
              QR code abaixo.
            </p>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={qrcode} alt="QR code de pareamento da Z-API" className="max-w-xs" />
            <p className="text-xs text-muted">Aguardando pareamento...</p>
            <button
              type="button"
              onClick={backToPicker}
              className="w-fit font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
            >
              Cancelar
            </button>
          </div>
        ) : (
          <form onSubmit={handleZApiSubmit} className="flex max-w-md flex-col gap-4">
            <button
              type="button"
              onClick={backToPicker}
              className="w-fit font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
            >
              ← Escolher outro provedor
            </button>
            <label className="flex flex-col gap-1 text-sm text-ink">
              Instance ID
              <input
                required
                value={zapiForm.instance_id}
                onChange={(event) =>
                  setZapiForm({ ...zapiForm, instance_id: event.target.value })
                }
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-ink">
              Token
              <input
                required
                type="password"
                value={zapiForm.instance_token}
                onChange={(event) =>
                  setZapiForm({ ...zapiForm, instance_token: event.target.value })
                }
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm text-ink">
              Client-Token
              <input
                required
                type="password"
                value={zapiForm.client_token}
                onChange={(event) =>
                  setZapiForm({ ...zapiForm, client_token: event.target.value })
                }
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
                    setZapiForm(EMPTY_ZAPI_FORM);
                  }}
                  className="font-mono text-xs uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                >
                  Cancelar
                </button>
              )}
            </div>
          </form>
        )}

        {showMetaInstructions && (
          <section className="mt-8 max-w-xl rounded border border-line bg-surface p-6">
            <h2 className="font-display text-base font-semibold text-ink">
              Conectar o WhatsApp Business
            </h2>
            <p className="mt-1 text-sm text-muted">
              Essa conexão é feita direto com a Meta (a empresa dona do WhatsApp) — só precisa
              ser feita uma única vez.
            </p>
            <ol className="mt-4 flex list-decimal flex-col gap-3 pl-5 text-sm text-ink">
              <li>
                Consiga um número de telefone que ainda não esteja em uso no WhatsApp comum
                nem no WhatsApp Business App — pode ser um chip novo, comprado só pra isso, ou
                um número que o escritório já tenha disponível.
                <span className="mt-0.5 block text-xs text-muted">
                  É esse número que vai enviar e receber as mensagens dos seus clientes — ele
                  fica exclusivo pra isso, então recomendamos não ser o número pessoal de
                  ninguém.
                </span>
              </li>
              <li>
                Acesse{" "}
                <a
                  href="https://developers.facebook.com/apps/"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent underline"
                >
                  developers.facebook.com
                </a>{" "}
                e crie um app pro seu escritório.
                <span className="mt-0.5 block text-xs text-muted">
                  É gratuito e leva 1 minuto — só um cadastro técnico exigido pelo WhatsApp, não
                  afeta seu uso normal do Facebook.
                </span>
              </li>
              <li>
                Dentro do app, você vai criar uma{" "}
                <a
                  href="https://business.facebook.com/settings/system-users"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent underline"
                >
                  &quot;conta de sistema&quot;
                </a>
                .
                <span className="mt-0.5 block text-xs text-muted">
                  Pense nela como um crachá de acesso que representa seu escritório perante o
                  WhatsApp, separado da sua conta pessoal.
                </span>
              </li>
              <li>
                Gere uma chave de acesso pra essa conta — é como uma senha que a plataforma vai
                usar pra mandar e receber mensagens em nome do seu escritório. Marque as duas
                opções de permissão do WhatsApp que aparecerem.
                <span className="mt-0.5 block text-xs text-muted">
                  Não tem erro — são só essas duas opções mesmo, pode marcar as duas.
                </span>
              </li>
              <li>
                Cadastre o{" "}
                <a
                  href="https://business.facebook.com/wa/manage/phone-numbers/"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent underline"
                >
                  número de telefone
                </a>{" "}
                do escritório. A Meta vai pedir um código de 6 dígitos pra confirmar.
                <span className="mt-0.5 block text-xs text-muted">
                  Você inventa esse código na hora — só serve pra essa confirmação, não precisa
                  anotar.
                </span>
              </li>
              <li>
                No painel do seu app, abra{" "}
                <span className="font-medium">WhatsApp → Configuration → Webhook</span> e clique
                em Edit.
                <span className="mt-0.5 block text-xs text-muted">
                  É essa tela que recebe as mensagens dos seus clientes e repassa pra Advoxs.
                </span>
              </li>
              <li>
                Cole os dois valores abaixo exatamente como estão e clique em Verify and save:
                <div className="mt-2 flex flex-col gap-2">
                  <div className="flex items-center gap-2">
                    <input
                      readOnly
                      aria-label="Callback URL"
                      value={webhookConfig!.callback_url}
                      className="flex-1 rounded border border-line bg-ground px-3 py-2 font-mono text-xs text-ink"
                    />
                    <button
                      type="button"
                      aria-label="Copiar Callback URL"
                      onClick={() => void handleCopy("url", webhookConfig!.callback_url)}
                      className="rounded border border-line px-3 py-2 font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-ink"
                    >
                      {copied === "url" ? "Copiado!" : "Copiar"}
                    </button>
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      readOnly
                      aria-label="Verify token"
                      value={webhookConfig!.verify_token}
                      className="flex-1 rounded border border-line bg-ground px-3 py-2 font-mono text-xs text-ink"
                    />
                    <button
                      type="button"
                      aria-label="Copiar Verify token"
                      onClick={() => void handleCopy("token", webhookConfig!.verify_token)}
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
                <span className="mt-0.5 block text-xs text-muted">
                  Sem assinar esse campo específico, o webhook fica configurado mas nunca é
                  acionado.
                </span>
              </li>
            </ol>
          </section>
        )}

        {showZApiInstructions && (
          <section className="mt-8 max-w-xl rounded border border-line bg-surface p-6">
            <h2 className="font-display text-base font-semibold text-ink">
              Conectar via Z-API
            </h2>
            <p className="mt-1 text-sm text-muted">
              A Z-API é um provedor independente (não é a Meta) — a conexão é por QR code, sem
              aprovação de negócio. Você só precisa buscar duas informações no painel da Z-API e
              colar no formulário acima.
            </p>
            <ol className="mt-4 flex list-decimal flex-col gap-3 pl-5 text-sm text-ink">
              <li>
                Crie uma conta em{" "}
                <a
                  href="https://app.z-api.io/app/auth/new-account"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent underline"
                >
                  app.z-api.io
                </a>{" "}
                (ou entre, se já tiver uma).
                <span className="mt-0.5 block text-xs text-muted">
                  Leva menos de 1 minuto e tem período de teste gratuito.
                </span>
              </li>
              <li>
                No painel, crie uma instância — pode dar o nome do escritório, é só pra você
                identificar depois.
                <span className="mt-0.5 block text-xs text-muted">
                  Cada instância representa uma conexão de WhatsApp; se você tiver mais de uma,
                  use a instância dedicada ao número que vai atender pela Advoxs.
                </span>
              </li>
              <li>
                Clique em <span className="font-medium">Editar</span> na instância criada — a
                tela mostra o <span className="font-medium">Instance ID</span> e o{" "}
                <span className="font-medium">Token</span>. Copie os dois e cole aqui no
                formulário.
                <span className="mt-0.5 block text-xs text-muted">
                  Não compartilhe esses dois valores com ninguém — eles dão acesso total a essa
                  instância.
                </span>
              </li>
              <li>
                Também copie o <span className="font-medium">Client-Token</span>, na aba{" "}
                <span className="font-medium">Segurança → Token de Segurança da Conta</span> do
                painel da Z-API — obrigatório, gere um se ainda não tiver.
              </li>
              <li>
                Clique em <span className="font-medium">Conectar</span>: o QR code aparece na
                hora. Abra o WhatsApp do número que vai atender, vá em{" "}
                <span className="font-medium">Aparelhos conectados</span> e escaneie.
                <span className="mt-0.5 block text-xs text-muted">
                  Diferente da Meta, não tem nenhum passo manual de webhook aqui — a Advoxs
                  configura isso automaticamente na Z-API no momento da conexão.
                </span>
              </li>
            </ol>
          </section>
        )}
      </div>
    </main>
  );
}
