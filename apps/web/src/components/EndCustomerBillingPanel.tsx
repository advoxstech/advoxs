"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { backendFetch } from "@/lib/client-api";

type Settings = {
  tenant_id: string;
  enabled: boolean;
  billing_mode: string;
  stripe_secret_key_configured: boolean;
  stripe_webhook_secret_configured: boolean;
  end_customer_tokens_per_credit: number | null;
};

const EMPTY_SETTINGS: Settings = {
  tenant_id: "",
  enabled: false,
  billing_mode: "credits",
  stripe_secret_key_configured: false,
  stripe_webhook_secret_configured: false,
  end_customer_tokens_per_credit: null,
};

function webhookUrlFor(tenantId: string): string {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
  return `${apiUrl}/api/v1/webhooks/stripe/tenant/${tenantId}`;
}

type Package = {
  id: string;
  name: string;
  price_brl: string;
  credits_granted: number;
  active: boolean;
};

const EMPTY_PACKAGE_FORM = { name: "", price_brl: "", credits_granted: "" };

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
  const [tokensPerCredit, setTokensPerCredit] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
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
        setTokensPerCredit(body.end_customer_tokens_per_credit?.toString() ?? "");
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
      if (tokensPerCredit) body.end_customer_tokens_per_credit = Number(tokensPerCredit);

      const response = await backendFetch("end-customer-billing/settings", {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      const responseBody = await response.json().catch(() => null);
      if (!response.ok) {
        setFeedback(extractErrorDetail(responseBody, "Falha ao salvar — tente novamente."));
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

  async function handleCreatePackage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);
    setCreatingPackage(true);
    try {
      const response = await backendFetch("end-customer-billing/packages", {
        method: "POST",
        body: JSON.stringify({
          name: packageForm.name,
          price_brl: packageForm.price_brl,
          credits_granted: Number(packageForm.credits_granted),
        }),
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
          Use a sua própria conta Stripe para vender créditos aos seus clientes finais.
        </p>
      </header>

      {feedback && (
        <p role="alert" className="border-b border-line bg-danger/5 px-8 py-3 text-sm text-danger">
          {feedback}
        </p>
      )}

      <div className="flex-1 overflow-y-auto px-8 py-6">
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
            Secret Key da Stripe {settings.stripe_secret_key_configured && "(configurada)"}
            <input
              type="password"
              value={secretKey}
              onChange={(event) => setSecretKey(event.target.value)}
              placeholder={settings.stripe_secret_key_configured ? "••••••••" : "sk_..."}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
          {settings.tenant_id && (
            <div className="flex flex-col gap-1 text-sm text-ink">
              URL do webhook
              <code className="break-all rounded border border-line bg-surface px-3 py-2 text-xs text-muted">
                {webhookUrlFor(settings.tenant_id)}
              </code>
              <p className="text-xs text-muted">
                Crie um endpoint com essa URL no Dashboard da sua conta Stripe (evento{" "}
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
          <label className="flex flex-col gap-1 text-sm text-ink">
            Tokens por crédito
            <input
              type="number"
              min={1}
              value={tokensPerCredit}
              onChange={(event) => setTokensPerCredit(event.target.value)}
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

        <hr className="my-6 border-line" />

        <h2 className="font-display text-lg font-semibold text-ink">Pacotes de crédito</h2>
        <ul className="mt-4 max-w-md">
          {packages.length === 0 && (
            <li className="py-4 text-sm text-muted">Nenhum pacote cadastrado ainda.</li>
          )}
          {packages.map((pkg) => (
            <li key={pkg.id} className="flex items-center justify-between border-b border-line py-3">
              <div>
                <p className="font-medium text-ink">{pkg.name}</p>
                <p className="text-xs text-muted">
                  R$ {pkg.price_brl} · {pkg.credits_granted} créditos
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
          <label className="flex flex-col gap-1 text-sm text-ink">
            Créditos
            <input
              required
              type="number"
              min={1}
              value={packageForm.credits_granted}
              onChange={(event) => setPackageForm({ ...packageForm, credits_granted: event.target.value })}
              className="rounded border border-line bg-surface px-3 py-2 text-sm text-ink"
            />
          </label>
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
