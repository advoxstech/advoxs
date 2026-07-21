"use client";

import { useEffect, useState } from "react";

import { backendFetch } from "@/lib/client-api";

type WebhookConfig = { callback_url: string; verify_token: string };

export function OnboardingWizard() {
  const [step, setStep] = useState(1);
  const [webhookConfig, setWebhookConfig] = useState<WebhookConfig | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);

  async function completeAndGo(href: string) {
    // Guard de duplo-clique (mesmo padrão do /creditos): a navegação não é
    // instantânea, então sem isso um segundo clique dispararia outro POST.
    if (leaving) return;
    setLeaving(true);
    try {
      await backendFetch("onboarding/complete", { method: "POST" });
    } catch {
      // Best-effort: pior caso o wizard reaparece no próximo login.
    }
    window.location.assign(href);
  }

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
              É pelo WhatsApp que os agentes vão atender seus clientes. Essa conexão é
              feita direto com a Meta (a empresa dona do WhatsApp) — é uma configuração
              técnica, mas só precisa ser feita uma vez.
            </p>
            <p className="mt-3 rounded-sm border border-line bg-surface px-4 py-3 text-sm leading-relaxed text-muted">
              Se travar em qualquer passo abaixo, manda um print pra gente que ajudamos a
              configurar — não precisa resolver sozinho.
            </p>
            <ol className="mt-4 flex list-decimal flex-col gap-3 pl-5 text-sm text-ink">
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
                  É gratuito e leva 1 minuto — só um cadastro técnico exigido pelo
                  WhatsApp, não afeta seu uso normal do Facebook.
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
                  Pense nela como um crachá de acesso que representa seu escritório
                  perante o WhatsApp, separado da sua conta pessoal.
                </span>
              </li>
              <li>
                Gere uma chave de acesso pra essa conta — é como uma senha que a
                plataforma vai usar pra mandar e receber mensagens em nome do seu
                escritório. Marque as duas opções de permissão do WhatsApp que
                aparecerem.
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
                  Você inventa esse código na hora — só serve pra essa confirmação, não
                  precisa anotar.
                </span>
              </li>
              <li>
                Por fim, cole os dois valores abaixo numa tela de configuração do
                WhatsApp (chamada &quot;Webhooks&quot;, dentro do mesmo app que você criou):
                <span className="mt-0.5 block text-xs text-muted">
                  É isso que liga o número de vocês na nossa plataforma — depois disso,
                  as mensagens já chegam automaticamente.
                </span>
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
                disabled={leaving}
                className="rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink disabled:opacity-50"
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
              <li>
                Cole a chave secreta e o segredo de webhook da sua conta Stripe (você
                encontra isso em Configurações → Chaves de API, dentro do painel da
                Stripe).
              </li>
              <li>Cadastre os pacotes de crédito que você quer vender pros seus clientes.</li>
              <li>
                Copie a URL exibida na página e cole no Dashboard da sua Stripe, na
                seção de Webhooks (evento{" "}
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
                disabled={leaving}
                className="rounded-sm bg-accent px-4 py-2.5 text-sm font-medium text-surface transition-colors hover:bg-ink disabled:opacity-50"
              >
                Configurar cobrança
              </button>
              <button
                type="button"
                onClick={() => void completeAndGo("/inicio")}
                disabled={leaving}
                className="rounded-sm border border-line px-4 py-2.5 text-sm font-medium text-ink transition-colors hover:border-accent disabled:opacity-50"
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
            disabled={leaving}
            className="text-sm text-muted underline transition-colors hover:text-ink disabled:opacity-50"
          >
            Pular e testar os agentes
          </button>
        </footer>
      </div>
    </main>
  );
}
