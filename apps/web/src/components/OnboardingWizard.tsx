"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";

type WebhookConfig = { callback_url: string; verify_token: string };

async function completeAndGo(href: string) {
  try {
    await backendFetch("onboarding/complete", { method: "POST" });
  } catch {
    // Best-effort: pior caso o wizard reaparece no próximo login.
  }
  window.location.assign(href);
}

export function OnboardingWizard() {
  const [step, setStep] = useState(1);
  const [webhookConfig, setWebhookConfig] = useState<WebhookConfig | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    async function loadConfig() {
      try {
        const response = await backendFetch("whatsapp/webhook-config");
        if (response.ok) {
          const config = await response.json().catch(() => null);
          if (config?.callback_url && config?.verify_token) {
            setWebhookConfig(config);
          }
        }
      } catch {
        // sem config, o passo 2 fica só com o texto
      }
    }
    void loadConfig();
  }, []);

  async function handleCopy(field: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(field);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      // clipboard indisponível — sem feedback, sem quebrar
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-ground px-6 py-10">
      <div className="w-full max-w-2xl">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted">
          Configurações iniciais · passo {step} de 3
        </p>

        {step === 1 && (
          <section className="mt-4">
            <h1 className="font-display text-3xl font-semibold text-ink">
              Bem-vindo à Advoxs
            </h1>
            <p className="mt-4 text-sm leading-relaxed text-ink">
              Seu escritório agora tem agentes de IA prontos pra atender clientes pelo
              WhatsApp: uma secretária faz a triagem e especialistas respondem dúvidas
              jurídicas, consultando a base de conhecimento que você subir. O consumo é
              pago em créditos — o pacote que você comprou já está na sua conta.
            </p>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              Vamos passar pelas duas configurações principais. Você pode fazer agora ou
              depois — tudo fica em Configurações.
            </p>
            <div className="mt-6">
              <button
                type="button"
                onClick={() => setStep(2)}
                className="rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink"
              >
                Começar
              </button>
            </div>
          </section>
        )}

        {step === 2 && (
          <section className="mt-4">
            <h1 className="font-display text-3xl font-semibold text-ink">
              Conectar o WhatsApp Business
            </h1>
            <p className="mt-4 text-sm leading-relaxed text-ink">
              É por ele que os agentes atendem seus clientes. O setup é feito no painel da
              Meta (developers.facebook.com) e depois colado aqui na plataforma:
            </p>
            <ol className="mt-3 flex list-decimal flex-col gap-2 pl-5 text-sm text-ink">
              <li>Crie (ou acesse) um app na Meta e adicione um System User com role Admin.</li>
              <li>
                Gere um token de acesso permanente com as permissões
                <code className="mx-1 rounded bg-surface px-1">whatsapp_business_management</code>
                e
                <code className="mx-1 rounded bg-surface px-1">whatsapp_business_messaging</code>.
              </li>
              <li>Adicione e verifique o número do escritório (você vai precisar do PIN de 2 fatores).</li>
              <li>
                Configure o webhook do app com os valores abaixo e assine o campo{" "}
                <code className="rounded bg-surface px-1">messages</code>:
              </li>
            </ol>
            {webhookConfig && (
              <div className="mt-3 flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <input
                    readOnly
                    aria-label="Callback URL"
                    value={webhookConfig.callback_url}
                    className="flex-1 rounded border border-line bg-surface px-3 py-2 font-mono text-xs text-ink"
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
                    className="flex-1 rounded border border-line bg-surface px-3 py-2 font-mono text-xs text-ink"
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
            )}
            <p className="mt-3 text-sm leading-relaxed text-muted">
              Com tudo pronto na Meta, cole as credenciais na página de configuração — a
              plataforma valida, registra o número e ativa o recebimento automaticamente.
            </p>
            <div className="mt-6 flex items-center gap-4">
              <button
                type="button"
                onClick={() => void completeAndGo("/configuracoes/whatsapp")}
                className="rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink"
              >
                Configurar WhatsApp agora
              </button>
              <button
                type="button"
                onClick={() => setStep(3)}
                className="rounded-sm border border-line px-4 py-2.5 text-sm font-medium text-ink transition-colors hover:border-accent"
              >
                Próximo
              </button>
            </div>
          </section>
        )}

        {step === 3 && (
          <section className="mt-4">
            <h1 className="font-display text-3xl font-semibold text-ink">
              Cobrança de clientes (opcional)
            </h1>
            <p className="mt-4 text-sm leading-relaxed text-ink">
              Se quiser, o escritório pode cobrar os próprios clientes pelo atendimento dos
              agentes: eles compram créditos seus, pagos direto na SUA conta Stripe — a
              plataforma nunca toca nesse dinheiro.
            </p>
            <ol className="mt-3 flex list-decimal flex-col gap-2 pl-5 text-sm text-ink">
              <li>Cole a secret key e o webhook secret da sua conta Stripe.</li>
              <li>Defina a conversão de tokens por crédito e cadastre seus pacotes.</li>
              <li>
                Aponte um webhook da sua Stripe pra URL exibida na página (evento{" "}
                <code className="rounded bg-surface px-1">checkout.session.completed</code>).
              </li>
            </ol>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              Sem configurar, os agentes atendem seus clientes normalmente, sem cobrança.
            </p>
            <div className="mt-6 flex items-center gap-4">
              <button
                type="button"
                onClick={() => void completeAndGo("/configuracoes/cobranca-clientes")}
                className="rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink"
              >
                Configurar cobrança
              </button>
              <button
                type="button"
                onClick={() => void completeAndGo("/inicio")}
                className="rounded-sm border border-line px-4 py-2.5 text-sm font-medium text-ink transition-colors hover:border-accent"
              >
                Concluir
              </button>
            </div>
          </section>
        )}

        <footer className="mt-10 border-t border-line pt-4">
          <button
            type="button"
            onClick={() => void completeAndGo("/conversas?aba=testes")}
            className="text-sm text-muted underline transition-colors hover:text-ink"
          >
            Pular e testar os agentes
          </button>
        </footer>
      </div>
    </main>
  );
}
