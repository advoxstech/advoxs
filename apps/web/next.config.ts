import type { NextConfig } from "next";

// Cabeçalhos de segurança aplicados a todas as respostas do painel.
// - Iframe: só o próprio domínio pode emoldurar páginas (a prévia do
//   timbrado em /perfil usa um iframe same-origin), então SAMEORIGIN e
//   `frame-ancestors 'self'`, nunca DENY.
// - COOP `same-origin-allow-popups`: o login do Google Drive (token model)
//   abre um popup que precisa conversar com a janela de origem.
// - CSP ainda não é aplicada: scripts inline do Next.js, Cloudflare,
//   Google Picker e Stripe Connect exigem uma política calibrada antes.
const securityHeaders = [
  {
    key: "Strict-Transport-Security",
    value: "max-age=31536000; includeSubDomains",
  },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "SAMEORIGIN" },
  { key: "Content-Security-Policy", value: "frame-ancestors 'self'" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), browsing-topics=()",
  },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin-allow-popups" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  transpilePackages: ["@advoxs/ui", "@advoxs/types"],
  devIndicators: false,
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      {
        // `:path*` também casa com o próprio /admin (zero segmentos).
        source: "/admin/:path*",
        headers: [{ key: "X-Robots-Tag", value: "noindex, nofollow" }],
      },
    ];
  },
};

export default nextConfig;
