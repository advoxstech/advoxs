import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/**/*.{ts,tsx}",
    "../../packages/ui/src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        ground: "var(--ground)",
        surface: "var(--surface)",
        ink: "var(--text)",
        muted: "var(--muted)",
        line: "var(--line)",
        accent: "var(--accent)",
        "accent-soft": "var(--accent-soft)",
        brass: "var(--brass)",
        "brass-soft": "var(--brass-soft)",
        "brass-ink": "var(--brass-ink)",
        danger: "var(--danger)",
        "nav-bg": "var(--nav-bg)",
        "nav-bg-2": "var(--nav-bg-2)",
        "nav-active": "var(--nav-active)",
        "nav-ink": "var(--nav-ink)",
        "nav-ink-muted": "var(--nav-ink-muted)",
        "auth-accent": "var(--auth-accent)",
        "auth-accent-soft": "var(--auth-accent-soft)",
        "auth-accent-ink": "var(--auth-accent-ink)",
      },
      // Piso de legibilidade: nada funcional abaixo de 11px no painel.
      fontSize: {
        micro: ["11px", { lineHeight: "1.4" }],
        action: ["13px", { lineHeight: "1.3", fontWeight: "500" }],
      },
      fontFamily: {
        display: ["var(--font-spectral)", "Georgia", "serif"],
        sans: ["var(--font-plex-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-plex-mono)", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
