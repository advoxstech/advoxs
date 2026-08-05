"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ConnectAccountOnboarding } from "@/components/ConnectAccountOnboarding";
import { ConnectEarnings } from "@/components/ConnectEarnings";
import { backendFetch } from "@/lib/client-api";

type Settings = {
  tenant_id: string;
  enabled: boolean;
  billing_mode: string;
  billing_provider: string;
  stripe_account_id: string | null;
  stripe_account_status: string | null;
  stripe_secret_key_configured: boolean;
  stripe_webhook_secret_configured: boolean;
  end_customer_tokens_per_credit: number | null;
  // URL completa (com domínio), montada no backend — nunca no client (ver
  // app/api/v1/end_customer_billing.py:_webhook_url_for).
  webhook_url: string;
};

const EMPTY_SETTINGS: Settings = {
  tenant_id: "",
  enabled: false,
  billing_mode: "credits",
  billing_provider: "standalone",
  stripe_account_id: null,
  stripe_account_status: null,
  stripe_secret_key_configured: false,
  stripe_webhook_secret_configured: false,
  end_customer_tokens_per_credit: null,
  webhook_url: "",
};

type Package = {
  id: string;
  name: string;
  price_brl: string;
  kind: string;
  credits_granted: number | null;
  active: boolean;
};

const EMPTY_PACKAGE_FORM = { name: "", price_brl: "", credits_granted: "", kind: "one_time" };

function extractErrorDetail(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export function EndCustomerBillingPanel() {
  const [settings, setSettings] = useState<Settings>(EMPTY_SETTINGS);
  const [loaded, setLoaded] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [secretKey, setSecretKey] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savingEnabledToggle, setSavingEnabledToggle] = useState(false);
  const [packages, setPackages] = useState<Package[]>([]);
  const [packageForm, setPackageForm] = useState(EMPTY_PACKAGE_FORM);
  const [creatingPackage, setCreatingPackage] = useState(false);

  async function load() {
    try {
      const [settingsResponse, packagesResponse] = await Promise.all([
        backendFetch("end-customer-billing/settings"),
        backendFetch("end-customer-billing/packages"),
      ]);
      if (settingsResponse.ok) {
        const body: Settings = await settingsResponse.json();
        setSettings(body);
        setEnabled(body.enabled);
      }
      if (packagesResponse.ok) {
        setPackages(await packagesResponse.json());
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
    setSaving(true);
    try {
      const body: Record<string, unknown> = { enabled };
      if (secretKey) body.stripe_secret_key = secretKey;
      if (webhookSecret) body.stripe_webhook_secret = webhookSecret;

      const response = await backendFetch("end-customer-billing/settings", {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      const responseBody = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(responseBody, "Falha ao salvar — tente novamente."));
        // Reverte o checkbox pro último valor confirmado pelo servidor — sem
        // isso a caixa fica marcada mesmo com o PATCH tendo falhado, dando a
        // impressão falsa de que salvou.
        setEnabled(settings.enabled);
        return;
      }
      setSettings(responseBody);
      setSecretKey("");
      setWebhookSecret("");
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setSaving(false);
    }
  }

  // Contraparte de `handleSubmit` pro branch Connect: lá o formulário também
  // carrega `stripe_secret_key`/`stripe_webhook_secret` (irrelevantes pra
  // tenants Connect, que autenticam via conta conectada, não secret key
  // avulsa) — aqui o PATCH manda só `{enabled}`.
  async function handleConnectEnabledSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setSavingEnabledToggle(true);
    try {
      const response = await backendFetch("end-customer-billing/settings", {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      });
      const responseBody = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(responseBody, "Falha ao salvar — tente novamente."));
        setEnabled(settings.enabled);
        return;
      }
      setSettings(responseBody);
      setEnabled(responseBody.enabled);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setSavingEnabledToggle(false);
    }
  }

  async function handleCreatePackage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setCreatingPackage(true);
    try {
      const requestBody: Record<string, unknown> = {
        name: packageForm.name,
        price_brl: packageForm.price_brl,
        kind: packageForm.kind,
      };
      if (packageForm.kind !== "subscription") {
        requestBody.credits_granted = Number(packageForm.credits_granted);
      }
      const response = await backendFetch("end-customer-billing/packages", {
        method: "POST",
        body: JSON.stringify(requestBody),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(body, "Falha ao criar pacote — tente novamente."));
        return;
      }
      setPackages([...packages, body]);
      setPackageForm(EMPTY_PACKAGE_FORM);
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
    } finally {
      setCreatingPackage(false);
    }
  }

  async function handleDeletePackage(pkg: Package) {
    if (!window.confirm(`Excluir o pacote "${pkg.name}"?`)) return;
    try {
      const response = await backendFetch(`end-customer-billing/packages/${pkg.id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setFeedback(extractErrorDetail(body, "Falha ao excluir — tente novamente."));
        return;
      }
      setPackages(packages.filter((p) => p.id !== pkg.id));
    } catch {
      setFeedback("Falha de conexão — tente novamente.");
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
        <h1 className="font-display text-xl font-semibold text-ink">Cobrança dos clientes</h1>
        <p className="text-sm text-muted">
          Use sua própria conta de pagamentos para vender créditos aos seus clientes finais.
        </p>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {/*
          "standalone" é o valor neutro da coluna — não é um sinal positivo
          de que o tenant configurou o modelo antigo. `GET /settings` sem
          row ainda (tenant novo) devolve exatamente esse valor default, com
          stripe_secret_key_configured=false. O sinal real de "já configurou
          o modelo antigo" é stripe_secret_key_configured=true — o backend
          (update_settings) sempre exigiu a secret key antes de habilitar a
          cobrança, então nenhum tenant chega a enabled=true sem ela. Por
          isso: mostra o onboarding Connect quando já está no Connect OU
          quando nunca configurou a secret key (tenant novo/não configurado)
          — só cai no formulário antigo quem já tinha configurado antes da
          migração pro Connect (grandfathered).
        */}
        {settings.billing_provider === "connect" || !settings.stripe_secret_key_configured ? (
          <section className="mb-8 max-w-xl">
            <h2 className="font-display text-base font-semibold text-ink">
              Configuração de pagamentos
            </h2>
            <p className="mt-1 text-sm text-muted">
              Preencha os dados abaixo pra receber os pagamentos dos seus clientes direto na
              sua conta — sem sair desta tela.
            </p>
            {settings.stripe_account_status === "active" ? (
              <>
                <p className="mt-4 inline-flex w-fit items-center gap-2 rounded-full bg-accent-soft px-3 py-1 font-mono text-[10px] uppercase tracking-[0.15em] text-accent">
                  Sua conta de pagamentos já está configurada
                </p>
                <ConnectEarnings />
              </>
            ) : settings.stripe_account_status === "in_review" ? (
              <div className="mt-4">
                <p className="text-sm text-muted">
                  Sua conta está em análise. Isso pode levar alguns minutos — não é necessário
                  preencher os dados de novo.
                </p>
                <ConnectAccountOnboarding visible={false} />
              </div>
            ) : (
              <div className="mt-4">
                <ConnectAccountOnboarding />
              </div>
            )}
            <form onSubmit={handleConnectEnabledSubmit} className="mt-6 flex flex-col gap-3">
              <label className="flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(event) => setEnabled(event.target.checked)}
                  disabled={settings.stripe_account_status !== "active"}
                />
                Cobrar meus clientes pelo uso dos agentes
              </label>
              <button
                type="submit"
                disabled={savingEnabledToggle || settings.stripe_account_status !== "active"}
                className="w-fit rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
              >
                {savingEnabledToggle ? "Salvando..." : "Salvar alterações"}
              </button>
            </form>
          </section>
        ) : (
          <>
          <section className="mb-8 max-w-xl rounded border border-line bg-surface p-6">
            <h2 className="font-display text-base font-semibold text-ink">
              Como configurar a cobrança pelos seus clientes
            </h2>
            <p className="mt-1 text-sm text-muted">
              Isso é opcional — sem configurar, os agentes atendem seus clientes
              normalmente, sem nenhuma cobrança.
            </p>
            <ol className="mt-4 flex list-decimal flex-col gap-3 pl-5 text-sm text-ink">
              <li>
                Se seu escritório ainda não tem uma conta de pagamentos,{" "}
                <a
                  href="https://dashboard.stripe.com/register"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent underline"
                >
                  crie uma aqui
                </a>
                .
                <span className="mt-0.5 block text-xs text-muted">
                  É a plataforma que processa as cobranças dos seus clientes com segurança —
                  grátis pra criar, só cobra uma taxa pequena quando processar um pagamento
                  de verdade.
                </span>
              </li>
              <li>
                No painel da sua conta de pagamentos, gere uma{" "}
                <a
                  href="https://dashboard.stripe.com/apikeys"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent underline"
                >
                  chave restrita de API
                </a>
                , marcando só a permissão &quot;Checkout Sessions: Write&quot;.
                <span className="mt-0.5 block text-xs text-muted">
                  Isso limita o que essa chave pode fazer caso ela vaze algum dia — mais
                  seguro do que usar a chave secreta completa da sua conta.
                </span>
              </li>
              <li>Cole essa chave no campo abaixo.</li>
              <li>
                Cadastre os pacotes de crédito que você quer vender pros seus clientes
                (nome, preço e quantidade de créditos), na seção logo abaixo do
                formulário.
              </li>
              <li>
                Ainda no painel da sua conta de pagamentos, crie um{" "}
                <a
                  href="https://dashboard.stripe.com/webhooks"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent underline"
                >
                  destino de evento (webhook)
                </a>
                , escolhendo &quot;Sua conta&quot; como escopo e o evento{" "}
                <code className="rounded bg-ground px-1">checkout.session.completed</code>,
                apontando pra URL do webhook que aparece no formulário abaixo.
                <span className="mt-0.5 block text-xs text-muted">
                  É isso que avisa a gente quando um cliente seu termina de pagar.
                </span>
              </li>
              <li>
                Depois de criar, revele o &quot;Signing secret&quot; (começa com{" "}
                <code className="rounded bg-ground px-1">whsec_</code>) e cole no campo
                &quot;Webhook Secret&quot; abaixo.
              </li>
            </ol>
          </section>

          <form onSubmit={handleSubmit} className="flex max-w-md flex-col gap-4">
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(event) => setEnabled(event.target.checked)}
              />
              Cobrar meus clientes pelo uso dos agentes
            </label>
            <label className="flex flex-col gap-1 text-sm text-ink">
              Chave secreta (Secret Key) {settings.stripe_secret_key_configured && "(configurada)"}
              <input
                type="password"
                value={secretKey}
                onChange={(event) => setSecretKey(event.target.value)}
                placeholder={settings.stripe_secret_key_configured ? "••••••••" : "sk_..."}
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            {settings.webhook_url && (
              <div className="flex flex-col gap-1 text-sm text-ink">
                URL do webhook
                <code className="break-all rounded border border-line bg-surface px-3 py-2 text-xs text-muted">
                  {settings.webhook_url}
                </code>
                <p className="text-xs text-muted">
                  Crie um endpoint com essa URL no painel da sua conta de pagamentos (evento{" "}
                  <code>checkout.session.completed</code>) e cole o Webhook Secret gerado abaixo.
                </p>
              </div>
            )}
            <label className="flex flex-col gap-1 text-sm text-ink">
              Webhook Secret {settings.stripe_webhook_secret_configured && "(configurado)"}
              <input
                type="password"
                value={webhookSecret}
                onChange={(event) => setWebhookSecret(event.target.value)}
                placeholder={settings.stripe_webhook_secret_configured ? "••••••••" : "whsec_..."}
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <button
              type="submit"
              disabled={saving}
              className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
            >
              {saving ? "Salvando..." : "Salvar configuração"}
            </button>
          </form>
          </>
        )}

        <hr className="my-6 border-line" />

        <h2 className="font-display text-lg font-semibold text-ink">Pacotes de crédito</h2>
        <ul className="mt-4 max-w-md">
          {packages.length === 0 && (
            <li className="py-4 text-sm text-muted">Nenhum pacote cadastrado ainda.</li>
          )}
          {packages.map((pkg) => (
            <li key={pkg.id} className="flex items-center justify-between border-b border-line py-3">
              <div>
                <p className="font-medium text-ink">
                  {pkg.name}{" "}
                  <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted">
                    {pkg.kind === "subscription" ? "Mensal" : "Avulso"}
                  </span>
                </p>
                <p className="text-xs text-muted">
                  {pkg.kind === "subscription"
                    ? `R$ ${pkg.price_brl}/mês`
                    : `R$ ${pkg.price_brl} · ${pkg.credits_granted} créditos`}
                </p>
              </div>
              <button
                type="button"
                onClick={() => void handleDeletePackage(pkg)}
                className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted transition-colors hover:text-danger"
              >
                Excluir
              </button>
            </li>
          ))}
        </ul>

        <form onSubmit={handleCreatePackage} className="mt-4 flex max-w-md flex-col gap-4">
          {settings.billing_provider === "connect" && (
            <label className="flex flex-col gap-1 text-sm text-ink">
              Tipo de pacote
              <select
                value={packageForm.kind}
                onChange={(event) => setPackageForm({ ...packageForm, kind: event.target.value })}
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              >
                <option value="one_time">Avulso (créditos)</option>
                <option value="subscription">Assinatura mensal (ilimitado)</option>
              </select>
            </label>
          )}
          <label className="flex flex-col gap-1 text-sm text-ink">
            Nome do pacote
            <input
              required
              value={packageForm.name}
              onChange={(event) => setPackageForm({ ...packageForm, name: event.target.value })}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-ink">
            Preço (R$)
            <input
              required
              value={packageForm.price_brl}
              onChange={(event) => setPackageForm({ ...packageForm, price_brl: event.target.value })}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          {packageForm.kind !== "subscription" && (
            <label className="flex flex-col gap-1 text-sm text-ink">
              Créditos
              <input
                required
                type="number"
                min={1}
                value={packageForm.credits_granted}
                onChange={(event) =>
                  setPackageForm({ ...packageForm, credits_granted: event.target.value })
                }
                className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
          )}
          <button
            type="submit"
            disabled={creatingPackage}
            className="rounded border border-line bg-surface px-4 py-2 font-mono text-xs uppercase tracking-[0.15em] text-ink transition-colors hover:border-accent disabled:opacity-50"
          >
            {creatingPackage ? "Adicionando..." : "Adicionar pacote"}
          </button>
        </form>
      </div>
    </main>
  );
}
